import { useEffect, useState } from "react";
import { User as UserIcon, X, ZoomIn } from "lucide-react";
import { resolveMediaUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface UserAvatarProps {
  src?: string | null | undefined;
  name?: string | null | undefined;
  className?: string | undefined;
  fallbackClassName?: string | undefined;
  iconClassName?: string | undefined;
  textClassName?: string | undefined;
  previewable?: boolean | undefined;
  onClick?: ((e: React.MouseEvent) => void) | undefined;
}

export function UserAvatar({
  src,
  name,
  className,
  fallbackClassName,
  iconClassName,
  textClassName,
  previewable = false,
  onClick,
}: UserAvatarProps) {
  const [hasError, setHasError] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  useEffect(() => {
    setHasError(false);
  }, [src]);

  const resolved = !hasError ? resolveMediaUrl(src) : null;
  const letter = (name || "").trim().slice(0, 1).toUpperCase();

  const handleClick = (e: React.MouseEvent) => {
    if (onClick) {
      onClick(e);
      return;
    }
    if (previewable) {
      e.stopPropagation();
      setPreviewOpen(true);
    }
  };

  const isInteractive = previewable || Boolean(onClick);

  return (
    <>
      {resolved ? (
        <img
          src={resolved}
          alt={name || "Avatar"}
          onClick={isInteractive ? handleClick : undefined}
          onError={() => setHasError(true)}
          className={cn(
            "rounded-full object-cover shrink-0 select-none",
            isInteractive && "cursor-pointer transition-transform hover:scale-105 active:scale-95",
            className,
          )}
        />
      ) : (
        <div
          onClick={isInteractive ? handleClick : undefined}
          className={cn(
            "flex items-center justify-center rounded-full bg-primary font-bold text-primary-foreground select-none shrink-0",
            isInteractive && "cursor-pointer transition-transform hover:scale-105 active:scale-95",
            fallbackClassName || className,
          )}
        >
          {letter ? (
            <span className={textClassName}>{letter}</span>
          ) : (
            <UserIcon className={cn("size-3.5", iconClassName)} />
          )}
        </div>
      )}

      {/* Full-size Avatar Preview Modal */}
      {previewable && (
        <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
          <DialogContent className="max-w-xs sm:max-w-sm rounded-3xl p-6 text-center border bg-card">
            <DialogHeader>
              <DialogTitle className="text-center font-display text-lg font-bold">
                {name || "Profile Picture"}
              </DialogTitle>
            </DialogHeader>

            <div className="my-4 flex items-center justify-center">
              {resolved ? (
                <img
                  src={resolved}
                  alt={name || "Profile"}
                  className="size-60 rounded-3xl object-cover border-2 border-primary/20 shadow-lift"
                />
              ) : (
                <div className="flex size-60 items-center justify-center rounded-3xl bg-primary text-primary-foreground font-display text-6xl font-bold shadow-lift">
                  {letter || <UserIcon className="size-20" />}
                </div>
              )}
            </div>

            <button
              onClick={() => setPreviewOpen(false)}
              className="mt-2 w-full rounded-xl bg-secondary py-2 text-xs font-semibold text-secondary-foreground hover:bg-muted transition"
            >
              Close
            </button>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
