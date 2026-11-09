import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, getTokens, WS_CHAT_URL, type ApiMessage, type UnreadOut } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { playChatChime } from "@/lib/sound";

type TypingState = Record<string, boolean>; // key: `${listing_id}|${user_id}` -> is_typing
type PresenceState = Record<string, boolean>; // key: user_id -> online
type SeenWatermarks = Record<string, string>; // key: `${listing_id}|${peer_id}` -> read_at ISO string

/** Global hook for application-wide unread counts across all active chats */
export function useUnreadChats() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["chat-unread"],
    queryFn: () => api<UnreadOut>("/chat/unread"),
    enabled: !!user?.id,
    refetchInterval: 12000,
    staleTime: 5000,
  });
}

export function useChatSocket(activeListing?: string, activeWith?: string) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [typingState, setTypingState] = useState<TypingState>({});
  const [presenceState, setPresenceState] = useState<PresenceState>({});
  const [lastSeenWatermarks, setLastSeenWatermarks] = useState<SeenWatermarks>({});

  // Visual arrival highlights: recently arrived messages & threads
  const [arrivedMsgIds, setArrivedMsgIds] = useState<Set<string>>(new Set());
  const [arrivedThreadKeys, setArrivedThreadKeys] = useState<Set<string>>(new Set());

  const activeListingRef = useRef(activeListing);
  activeListingRef.current = activeListing;
  const activeWithRef = useRef(activeWith);
  activeWithRef.current = activeWith;

  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastMarkedRead = useRef<Record<string, number>>({});

  const markRead = useCallback(
    (listingId: string, peerId: string) => {
      if (!listingId || !peerId) return;
      const key = `${listingId}|${peerId}`;
      const now = Date.now();
      // Throttle mark-read per conversation to at most once every 3 seconds
      if (now - (lastMarkedRead.current[key] ?? 0) < 3000) return;
      lastMarkedRead.current[key] = now;

      // 1. Send WebSocket frame if connected
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: "message.read",
            listing_id: listingId,
            peer_id: peerId,
          }),
        );
      }

      // 2. HTTP fallback for guarantee (safely throttled)
      api("/chat/read", {
        method: "POST",
        body: { listing_id: listingId, peer_id: peerId },
      }).catch(() => {});

      // 3. Immediately clear local unread count in cache
      qc.setQueryData<UnreadOut>(["chat-unread"], (old) => {
        if (!old) return old;
        const nextConvs = old.conversations.filter(
          (c) => !(c.listing_id === listingId && c.peer_id === peerId),
        );
        return {
          total: nextConvs.reduce((acc, c) => acc + c.unread, 0),
          conversations: nextConvs,
        };
      });

      // 4. Clear arrival highlight for this thread
      setArrivedThreadKeys((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    },
    [qc],
  );

  const watchPresence = useCallback((userIds: string[]) => {
    if (userIds.length > 0 && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "presence.watch",
          user_ids: userIds,
        }),
      );
    }
  }, []);

  const typingTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const lastTypingSent = useRef<number>(0);
  const startTyping = useCallback((listingId: string, recipientId: string) => {
    const now = Date.now();
    if (now - lastTypingSent.current < 1000) return;
    lastTypingSent.current = now;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "typing.start",
          listing_id: listingId,
          recipient_id: recipientId,
        }),
      );
    }
  }, []);

  const stopTyping = useCallback((listingId: string, recipientId: string) => {
    lastTypingSent.current = 0;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "typing.stop",
          listing_id: listingId,
          recipient_id: recipientId,
        }),
      );
    }
  }, []);

  const sendChatMessage = useCallback(
    (listingId: string, recipientId: string, body: string, clientId: string): boolean => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: "message.send",
            listing_id: listingId,
            recipient_id: recipientId,
            body,
            client_id: clientId,
          }),
        );
        return true;
      }
      return false;
    },
    [],
  );

  useEffect(() => {
    if (!user?.id) return;
    let isDestroyed = false;

    const connect = () => {
      const token = getTokens()?.access;
      if (!token || isDestroyed) return;

      const wsUrl = `${WS_CHAT_URL}?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (isDestroyed) {
          ws.close();
          return;
        }
        setIsConnected(true);

        // Immediate sync on connect/reconnect
        qc.invalidateQueries({ queryKey: ["messages"] });
        qc.invalidateQueries({ queryKey: ["chat-unread"] });

        // Start heartbeat ping every 25 seconds
        if (pingTimer.current) clearInterval(pingTimer.current);
        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
          }
        }, 25000);

        // If a chat is already open, watch presence and mark read
        if (activeWithRef.current) {
          ws.send(JSON.stringify({ type: "presence.watch", user_ids: [activeWithRef.current] }));
          if (activeListingRef.current) {
            markRead(activeListingRef.current, activeWithRef.current);
          }
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (!data || typeof data !== "object") return;

          switch (data.type) {
            case "ready": {
              // Connected and authenticated
              break;
            }
            case "message.new": {
              const newMsg = data.message as ApiMessage;
              if (newMsg && newMsg.id) {
                // Update messages cache
                qc.setQueryData<ApiMessage[]>(["messages"], (old = []) => {
                  if (old.some((m) => m.id === newMsg.id)) return old;
                  return [...old, newMsg];
                });
                qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => {
                  if (old.some((m) => m.id === newMsg.id)) return old;
                  return [...old, newMsg];
                });

                const isFromOther = newMsg.sender_id !== user.id;
                const isCurrentActive =
                  activeListingRef.current === newMsg.listing_id &&
                  (activeWithRef.current === newMsg.sender_id ||
                    activeWithRef.current === newMsg.recipient_id);

                if (isFromOther) {
                  // Play gentle audio arrival chime
                  playChatChime();

                  // Highlight message & thread
                  const threadKey = `${newMsg.listing_id}|${newMsg.sender_id}`;
                  setArrivedMsgIds((prev) => new Set([...prev, newMsg.id]));
                  setArrivedThreadKeys((prev) => new Set([...prev, threadKey]));

                  // Auto remove highlight after 6 seconds
                  setTimeout(() => {
                    setArrivedMsgIds((prev) => {
                      const next = new Set(prev);
                      next.delete(newMsg.id);
                      return next;
                    });
                    setArrivedThreadKeys((prev) => {
                      const next = new Set(prev);
                      next.delete(threadKey);
                      return next;
                    });
                  }, 6000);

                  if (isCurrentActive) {
                    // Mark as read immediately since user is actively on this screen
                    markRead(newMsg.listing_id, newMsg.sender_id);
                  } else {
                    // Increment unread count in ["chat-unread"]
                    qc.setQueryData<UnreadOut>(["chat-unread"], (old) => {
                      const convs = old?.conversations ? [...old.conversations] : [];
                      const idx = convs.findIndex(
                        (c) => c.listing_id === newMsg.listing_id && c.peer_id === newMsg.sender_id,
                      );
                      if (idx >= 0) {
                        convs[idx] = { ...convs[idx], unread: convs[idx].unread + 1 };
                      } else {
                        convs.push({
                          listing_id: newMsg.listing_id,
                          peer_id: newMsg.sender_id,
                          unread: 1,
                        });
                      }
                      return {
                        total: (old?.total ?? 0) + 1,
                        conversations: convs,
                      };
                    });

                    // Toast notification for incoming chat
                    toast.info(`New message from ${newMsg.sender_name}`, {
                      description: newMsg.body,
                      duration: 4500,
                    });
                  }
                }
              }
              break;
            }
            case "message.ack": {
              const ackMsg = data.message as ApiMessage;
              const clientId = data.client_id as string | undefined;
              if (ackMsg && ackMsg.id) {
                qc.setQueryData<ApiMessage[]>(["messages"], (old = []) => {
                  const idx = old.findIndex(
                    (m) => (clientId && m.id === clientId) || m.id === ackMsg.id,
                  );
                  if (idx !== -1) {
                    const copy = [...old];
                    copy[idx] = ackMsg;
                    return copy;
                  }
                  if (old.some((m) => m.id === ackMsg.id)) return old;
                  return [...old, ackMsg];
                });
                qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => {
                  const idx = old.findIndex(
                    (m) => (clientId && m.id === clientId) || m.id === ackMsg.id,
                  );
                  if (idx !== -1) {
                    const copy = [...old];
                    copy[idx] = ackMsg;
                    return copy;
                  }
                  if (old.some((m) => m.id === ackMsg.id)) return old;
                  return [...old, ackMsg];
                });
              }
              break;
            }
            case "read": {
              // Server broadcast: {"type":"read","listing_id":...,"reader_id":...,"peer_id":...,"read_at":...}
              const listingId = String(data.listing_id);
              const readerId = String(data.reader_id);
              const peerId = String(data.peer_id);
              const readAt = String(data.read_at);

              if (readerId === user.id) {
                // I read this conversation on another device -> clear unread badge
                qc.setQueryData<UnreadOut>(["chat-unread"], (old) => {
                  if (!old) return old;
                  const nextConvs = old.conversations.filter(
                    (c) => !(c.listing_id === listingId && c.peer_id === peerId),
                  );
                  return {
                    total: nextConvs.reduce((acc, c) => acc + c.unread, 0),
                    conversations: nextConvs,
                  };
                });
              } else if (readerId === peerId) {
                // Peer read my messages -> store seen watermark
                setLastSeenWatermarks((prev) => ({
                  ...prev,
                  [`${listingId}|${peerId}`]: readAt,
                }));
              }
              break;
            }
            case "typing": {
              const key = `${data.listing_id}|${data.user_id}`;
              const isTyping = Boolean(data.is_typing);
              setTypingState((prev) => ({
                ...prev,
                [key]: isTyping,
              }));
              if (typingTimers.current[key]) clearTimeout(typingTimers.current[key]);
              if (isTyping) {
                typingTimers.current[key] = setTimeout(() => {
                  setTypingState((prev) => ({ ...prev, [key]: false }));
                }, 3500);
              }
              break;
            }
            case "presence": {
              setPresenceState((prev) => ({
                ...prev,
                [data.user_id]: Boolean(data.online),
              }));
              break;
            }
            case "presence.state": {
              if (data.users && typeof data.users === "object") {
                const next: PresenceState = {};
                for (const [uid, info] of Object.entries(data.users)) {
                  next[uid] = Boolean((info as { online?: boolean }).online);
                }
                setPresenceState((prev) => ({ ...prev, ...next }));
              }
              break;
            }
            default:
              break;
          }
        } catch {
          // ignore non-json frames
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        if (pingTimer.current) clearInterval(pingTimer.current);
        if (!isDestroyed) {
          reconnectTimer.current = setTimeout(connect, 1000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    const activeTypingTimers = typingTimers.current;

    return () => {
      isDestroyed = true;
      if (pingTimer.current) clearInterval(pingTimer.current);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      Object.values(activeTypingTimers).forEach(clearTimeout);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [user?.id, qc, markRead]);

  // When active conversation changes, watch presence & mark read
  useEffect(() => {
    if (activeWith && isConnected) {
      watchPresence([activeWith]);
      if (activeListing) {
        markRead(activeListing, activeWith);
      }
    }
  }, [activeListing, activeWith, isConnected, watchPresence, markRead]);

  return {
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
    watchPresence,
  };
}
