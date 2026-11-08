import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getTokens, WS_CHAT_URL, type ApiMessage } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";

type TypingState = Record<string, boolean>; // key: `${listing_id}|${user_id}` -> is_typing
type PresenceState = Record<string, boolean>; // key: user_id -> online

export function useChatSocket(activeListing?: string, activeWith?: string) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [typingState, setTypingState] = useState<TypingState>({});
  const [presenceState, setPresenceState] = useState<PresenceState>({});

  const activeListingRef = useRef(activeListing);
  activeListingRef.current = activeListing;
  const activeWithRef = useRef(activeWith);
  activeWithRef.current = activeWith;

  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const markRead = useCallback((listingId: string, peerId: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "message.read",
          listing_id: listingId,
          peer_id: peerId,
        }),
      );
    }
  }, []);

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
            ws.send(
              JSON.stringify({
                type: "message.read",
                listing_id: activeListingRef.current,
                peer_id: activeWithRef.current,
              }),
            );
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
                qc.setQueryData<ApiMessage[]>(["messages"], (old = []) => {
                  if (old.some((m) => m.id === newMsg.id)) return old;
                  return [...old, newMsg];
                });
                qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => {
                  if (old.some((m) => m.id === newMsg.id)) return old;
                  return [...old, newMsg];
                });
                qc.invalidateQueries({ queryKey: ["messages"], refetchType: "all" });

                // If currently viewing this conversation, mark read
                if (
                  activeListingRef.current === newMsg.listing_id &&
                  (activeWithRef.current === newMsg.sender_id || activeWithRef.current === newMsg.recipient_id)
                ) {
                  const peer = newMsg.sender_id === user.id ? newMsg.recipient_id : newMsg.sender_id;
                  markRead(newMsg.listing_id, peer);
                }
              }
              break;
            }
            case "message.ack": {
              const ackMsg = data.message as ApiMessage;
              const clientId = data.client_id as string | undefined;
              if (ackMsg && ackMsg.id) {
                qc.setQueryData<ApiMessage[]>(["messages"], (old = []) => {
                  const idx = old.findIndex((m) => (clientId && m.id === clientId) || m.id === ackMsg.id);
                  if (idx !== -1) {
                    const copy = [...old];
                    copy[idx] = ackMsg;
                    return copy;
                  }
                  if (old.some((m) => m.id === ackMsg.id)) return old;
                  return [...old, ackMsg];
                });
                qc.setQueriesData<ApiMessage[]>({ queryKey: ["messages"] }, (old = []) => {
                  const idx = old.findIndex((m) => (clientId && m.id === clientId) || m.id === ackMsg.id);
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

    return () => {
      isDestroyed = true;
      if (pingTimer.current) clearInterval(pingTimer.current);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      Object.values(typingTimers.current).forEach(clearTimeout);
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
    sendChatMessage,
    startTyping,
    stopTyping,
    markRead,
    watchPresence,
  };
}
