import { RefreshCw } from "lucide-react";
import { usePwa } from "@/hooks/use-pwa";

export function PwaUpdateToast() {
  const { needRefresh, updateApp, triggerHaptic } = usePwa();

  if (!needRefresh) {
    return null;
  }

  const handleUpdate = () => {
    triggerHaptic("heavy");
    updateApp();
  };

  return (
    <aside
      aria-label="App update available"
      className="fixed inset-x-0 bottom-24 z-50 mx-auto max-w-sm px-4 animate-rise md:bottom-8"
    >
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-primary/30 bg-card/95 p-3.5 shadow-lift backdrop-blur-md">
        <div className="flex items-center gap-2.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <RefreshCw className="size-4 animate-spin" />
          </div>
          <div>
            <p className="text-xs font-bold text-foreground">Update Available</p>
            <p className="text-[11px] text-muted-foreground">A new version of Scrappe is ready.</p>
          </div>
        </div>

        <button
          onClick={handleUpdate}
          className="shrink-0 rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm transition active:scale-[0.98] hover:bg-primary/90"
        >
          Update Now
        </button>
      </div>
    </aside>
  );
}
