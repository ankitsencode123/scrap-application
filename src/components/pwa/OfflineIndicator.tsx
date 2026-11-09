import { useState, useEffect } from "react";
import { WifiOff, Wifi } from "lucide-react";
import { usePwa } from "@/hooks/use-pwa";

export function OfflineIndicator() {
  const { isOffline, triggerHaptic } = usePwa();
  const [wasOffline, setWasOffline] = useState(false);
  const [showReconnected, setShowReconnected] = useState(false);

  useEffect(() => {
    if (isOffline) {
      setWasOffline(true);
      triggerHaptic("medium");
    } else if (wasOffline) {
      // Just reconnected
      setShowReconnected(true);
      triggerHaptic("success");
      const timer = setTimeout(() => {
        setShowReconnected(false);
        setWasOffline(false);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [isOffline, wasOffline, triggerHaptic]);

  if (!isOffline && !showReconnected) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-3 z-50 flex justify-center px-4 pointer-events-none"
    >
      {isOffline ? (
        <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-destructive/20 bg-destructive/90 px-4 py-1.5 text-xs font-semibold text-destructive-foreground shadow-lift backdrop-blur-md animate-rise">
          <WifiOff className="size-3.5 animate-pulse" />
          <span>Offline Mode — Viewing cached data</span>
        </div>
      ) : showReconnected ? (
        <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-success/30 bg-success/90 px-4 py-1.5 text-xs font-semibold text-white shadow-lift backdrop-blur-md animate-rise">
          <Wifi className="size-3.5" />
          <span>Back online! Reconnected to Scrappe</span>
        </div>
      ) : null}
    </div>
  );
}
