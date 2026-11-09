import { useState, useEffect } from "react";
import { Download, Share2, X, Sparkles, CheckCircle2 } from "lucide-react";
import { usePwa } from "@/hooks/use-pwa";
import { cn } from "@/lib/utils";

const DISMISSED_KEY = "scrappe_pwa_prompt_dismissed_until";
const DISMISS_DAYS = 7;

export function PwaInstallPrompt() {
  const { isInstallable, isInstalled, isIOS, installApp, triggerHaptic } = usePwa();
  const [isVisible, setIsVisible] = useState(false);
  const [showIosGuide, setShowIosGuide] = useState(false);

  useEffect(() => {
    // If already running standalone (installed), never show prompt
    if (isInstalled) {
      setIsVisible(false);
      return;
    }

    // Check if dismissed recently
    const dismissedUntil = localStorage.getItem(DISMISSED_KEY);
    if (dismissedUntil && Number(dismissedUntil) > Date.now()) {
      return;
    }

    // On Android/desktop Chrome with beforeinstallprompt or on iOS mobile Safari
    if (isInstallable || isIOS) {
      const timer = setTimeout(() => {
        setIsVisible(true);
      }, 2500); // polite delay after page loads
      return () => clearTimeout(timer);
    }
  }, [isInstallable, isInstalled, isIOS]);

  const handleDismiss = () => {
    triggerHaptic("light");
    setIsVisible(false);
    setShowIosGuide(false);
    const expireTime = Date.now() + DISMISS_DAYS * 24 * 60 * 60 * 1000;
    localStorage.setItem(DISMISSED_KEY, expireTime.toString());
  };

  const handleInstallClick = async () => {
    triggerHaptic("medium");
    if (isIOS) {
      setShowIosGuide(true);
    } else {
      await installApp();
    }
  };

  if (!isVisible || isInstalled) {
    return null;
  }

  return (
    <aside
      aria-label="Install App"
      className="fixed inset-x-0 bottom-20 z-50 mx-auto max-w-md px-4 pb-safe animate-rise md:bottom-6"
    >
      <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-card/95 p-4 shadow-lift backdrop-blur-md">
        {/* Ambient background glow */}
        <div className="pointer-events-none absolute -right-12 -top-12 size-36 rounded-full bg-sun/20 blur-2xl" />

        {/* Dismiss Button */}
        <button
          onClick={handleDismiss}
          className="absolute right-3 top-3 rounded-full p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label="Close install prompt"
        >
          <X className="size-4" />
        </button>

        <div className="flex items-start gap-3.5 pr-6">
          {/* App Icon */}
          <div className="relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-primary shadow-soft">
            <img
              src="/icons/icon-192.png"
              alt="Scrappe Icon"
              className="size-full object-cover"
              onError={(e) => {
                // fallback to SVG if PNG loading fails
                (e.target as HTMLImageElement).src = "/favicon.svg";
              }}
            />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <h3 className="text-sm font-bold tracking-tight text-foreground">
                Install Scrappe App
              </h3>
              <span className="flex items-center gap-0.5 rounded-full bg-sun/20 px-1.5 py-0.5 text-[10px] font-semibold text-sun-foreground">
                <Sparkles className="size-2.5 text-sun" /> PWA
              </span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              Instant doorstep scrap pickup, fast offers & offline access right from your home
              screen.
            </p>
          </div>
        </div>

        {/* Feature bullets */}
        <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-2.5 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <CheckCircle2 className="size-3 text-primary" /> Instant cash offers
          </span>
          <span className="flex items-center gap-1">
            <CheckCircle2 className="size-3 text-primary" /> Live map tracking
          </span>
          <span className="flex items-center gap-1">
            <CheckCircle2 className="size-3 text-primary" /> Zero download size
          </span>
        </div>

        {/* Action button */}
        {!showIosGuide ? (
          <div className="mt-3.5 flex gap-2">
            <button
              onClick={handleInstallClick}
              className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-xs font-semibold text-primary-foreground shadow-soft transition active:scale-[0.98] hover:bg-primary/90"
            >
              {isIOS ? (
                <>
                  <Share2 className="size-3.5" />
                  Add to Home Screen
                </>
              ) : (
                <>
                  <Download className="size-3.5" />
                  Install Scrappe
                </>
              )}
            </button>
            <button
              onClick={handleDismiss}
              className="rounded-xl border border-border px-3 py-2.5 text-xs font-medium text-muted-foreground transition active:scale-[0.98] hover:bg-muted"
            >
              Later
            </button>
          </div>
        ) : (
          /* iOS Safari Step-by-Step Instructions */
          <div className="mt-3.5 rounded-xl bg-primary-soft/60 p-3 text-xs text-foreground animate-rise">
            <p className="font-semibold text-primary">To install on iPhone / iPad:</p>
            <ol className="mt-1.5 space-y-1 text-muted-foreground">
              <li className="flex items-center gap-1.5">
                <span className="flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
                  1
                </span>
                Tap the <Share2 className="inline size-3.5 text-primary" />{" "}
                <span className="font-medium text-foreground">Share</span> button in Safari.
              </li>
              <li className="flex items-center gap-1.5">
                <span className="flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
                  2
                </span>
                Scroll down and tap{" "}
                <span className="font-semibold text-foreground">Add to Home Screen</span>.
              </li>
            </ol>
            <button
              onClick={handleDismiss}
              className="mt-2.5 w-full rounded-lg bg-primary/10 py-1.5 text-center text-[11px] font-semibold text-primary transition hover:bg-primary/20"
            >
              Got it
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

/**
 * Reusable standalone button to install PWA (e.g. for Profile or Header)
 */
export function PwaInstallButton({ className }: { className?: string }) {
  const { isInstallable, isInstalled, isIOS, installApp, triggerHaptic } = usePwa();
  const [showIosModal, setShowIosModal] = useState(false);

  if (isInstalled) {
    return (
      <div
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full bg-primary-soft px-3 py-1 text-xs font-medium text-primary",
          className,
        )}
      >
        <CheckCircle2 className="size-3.5" /> App Installed
      </div>
    );
  }

  const handleClick = async () => {
    triggerHaptic("medium");
    if (isIOS) {
      setShowIosModal(true);
    } else if (isInstallable) {
      await installApp();
    } else {
      // Browser didn't support beforeinstallprompt or was already handled
      alert(
        "To install Scrappe, tap your browser's menu (⋮) and select 'Install Scrappe' or 'Add to Home screen'.",
      );
    }
  };

  return (
    <>
      <button
        onClick={handleClick}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm transition active:scale-[0.98] hover:bg-primary/90",
          className,
        )}
      >
        <Download className="size-3.5" />
        Install App
      </button>

      {showIosModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl bg-card p-5 shadow-lift">
            <h4 className="font-display text-base font-bold text-foreground">Install on iOS</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              Follow these quick steps in Safari to add Scrappe to your Home Screen:
            </p>
            <div className="mt-3 space-y-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-white">
                  1
                </span>
                <span>
                  Tap the <Share2 className="inline size-3.5 text-primary" /> button on Safari's
                  bottom toolbar.
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-white">
                  2
                </span>
                <span>
                  Select <strong>Add to Home Screen</strong>.
                </span>
              </div>
            </div>
            <button
              onClick={() => setShowIosModal(false)}
              className="mt-4 w-full rounded-xl bg-primary py-2 text-xs font-semibold text-white"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </>
  );
}
