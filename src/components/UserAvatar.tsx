import { useEffect, useState } from "react";
import { User as UserIcon } from "lucide-react";
import { resolveMediaUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

interface UserAvatarProps {
  src?: string | null;
  name?: string | null;
  className?: string;
  fallbackClassName?: string;
  iconClassName?: string;
  textClassName?: string;
}

export function UserAvatar({
  src,
  name,
  className,
  fallbackClassName,
  iconClassName,
  textClassName,
}: UserAvatarProps) {
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    setHasError(false);
  }, [src]);

  const resolved = !hasError ? resolveMediaUrl(src) : null;
  const letter = (name || "").trim().slice(0, 1).toUpperCase();

  if (resolved) {
    return (
      <img
        src={resolved}
        alt={name || "Avatar"}
        onError={() => setHasError(true)}
        className={cn("rounded-full object-cover shrink-0 select-none", className)}
      />
    );
  }

  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-full bg-primary font-bold text-primary-foreground select-none shrink-0",
        fallbackClassName || className
      )}
    >
      {letter ? (
        <span className={textClassName}>{letter}</span>
      ) : (
        <UserIcon className={cn("size-3.5", iconClassName)} />
      )}
    </div>
  );
}
