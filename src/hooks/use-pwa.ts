import { useState, useEffect, useCallback } from "react";

interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  readonly userChoice: Promise<{
    outcome: "accepted" | "dismissed";
    platform: string;
  }>;
  prompt(): Promise<void>;
}

declare global {
  interface WindowEventMap {
    beforeinstallprompt: BeforeInstallPromptEvent;
  }
}

export function usePwa() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isInstallable, setIsInstallable] = useState(false);
  const [isInstalled, setIsInstalled] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [needRefresh, setNeedRefresh] = useState(false);
  const [waitingWorker, setWaitingWorker] = useState<ServiceWorker | null>(null);
  const [isIOS, setIsIOS] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;

    // 1. Initial offline state
    setIsOffline(!navigator.onLine);

    const handleOnline = () => setIsOffline(false);
    const handleOffline = () => setIsOffline(true);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    // 2. Check if running as standalone PWA
    const checkIsStandalone = () => {
      const isStandaloneMedia = window.matchMedia("(display-mode: standalone)").matches;
      // Safari iOS standalone check
      const isIOSStandalone =
        (window.navigator as unknown as { standalone?: boolean }).standalone === true;
      return isStandaloneMedia || isIOSStandalone;
    };

    const standalone = checkIsStandalone();
    setIsInstalled(standalone);

    // 3. Detect iOS device
    const userAgent = window.navigator.userAgent.toLowerCase();
    const isIosDevice =
      /iphone|ipad|ipod/.test(userAgent) && !(window as unknown as { MSStream?: unknown }).MSStream;
    setIsIOS(isIosDevice);

    // 4. Capture beforeinstallprompt for Android / Chrome
    const handleBeforeInstallPrompt = (e: BeforeInstallPromptEvent) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setIsInstallable(true);
    };

    const handleAppInstalled = () => {
      setIsInstalled(true);
      setIsInstallable(false);
      setDeferredPrompt(null);
    };

    window.addEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
    window.addEventListener("appinstalled", handleAppInstalled);

    // 5. Register Service Worker in production / browser environment
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((registration) => {
          // Check if a new worker is waiting
          if (registration.waiting) {
            setWaitingWorker(registration.waiting);
            setNeedRefresh(true);
          }

          registration.addEventListener("updatefound", () => {
            const newWorker = registration.installing;
            if (newWorker) {
              newWorker.addEventListener("statechange", () => {
                if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
                  setWaitingWorker(newWorker);
                  setNeedRefresh(true);
                }
              });
            }
          });
        })
        .catch((err) => {
          console.warn("[PWA] Service Worker registration failed:", err);
        });

      // Handle controller change (reloads on update)
      let refreshing = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (!refreshing) {
          refreshing = true;
          window.location.reload();
        }
      });
    }

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
      window.removeEventListener("appinstalled", handleAppInstalled);
    };
  }, []);

  // Trigger browser install prompt
  const installApp = useCallback(async () => {
    if (!deferredPrompt) return;

    try {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === "accepted") {
        setIsInstalled(true);
      }
      setDeferredPrompt(null);
      setIsInstallable(false);
    } catch (err) {
      console.warn("[PWA] Prompt error:", err);
    }
  }, [deferredPrompt]);

  // Update App to latest version
  const updateApp = useCallback(() => {
    if (waitingWorker) {
      waitingWorker.postMessage({ type: "SKIP_WAITING" });
    } else {
      window.location.reload();
    }
  }, [waitingWorker]);

  // Mobile Haptic Feedback helper
  const triggerHaptic = useCallback((type: "light" | "medium" | "heavy" | "success" = "light") => {
    if (typeof window !== "undefined" && "vibrate" in navigator) {
      switch (type) {
        case "light":
          navigator.vibrate(8);
          break;
        case "medium":
          navigator.vibrate(15);
          break;
        case "heavy":
          navigator.vibrate(25);
          break;
        case "success":
          navigator.vibrate([10, 30, 20]);
          break;
      }
    }
  }, []);

  // Native Web Share helper
  const shareApp = useCallback(
    async (shareData?: { title?: string; text?: string; url?: string }) => {
      const data = {
        title: shareData?.title || "Scrappe — Doorstep Scrap Marketplace",
        text:
          shareData?.text || "Sell your scrap from home and get instant cash pickup with Scrappe!",
        url: shareData?.url || window.location.origin,
      };

      if (navigator.share) {
        try {
          await navigator.share(data);
          return true;
        } catch (err) {
          if ((err as Error).name !== "AbortError") {
            console.warn("[PWA] Share failed:", err);
          }
        }
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(data.url);
        return false; // indicates copied to clipboard instead
      }
      return false;
    },
    [],
  );

  return {
    isInstallable,
    isInstalled,
    isOffline,
    needRefresh,
    isIOS,
    installApp,
    updateApp,
    triggerHaptic,
    shareApp,
  };
}
