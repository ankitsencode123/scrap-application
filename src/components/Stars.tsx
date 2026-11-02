import { Star } from "lucide-react";
import { cn } from "@/lib/utils";

export function Stars({
  value,
  onChange,
  size = "size-4",
}: {
  value: number;
  onChange?: (v: number) => void;
  size?: string;
}) {
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((i) => (
        <button
          key={i}
          type="button"
          disabled={!onChange}
          onClick={() => onChange?.(i)}
          aria-label={`${i} star`}
          className={cn(!onChange && "cursor-default")}
        >
          <Star
            className={cn(size, i <= Math.round(value) ? "fill-accent text-accent" : "text-border")}
          />
        </button>
      ))}
    </div>
  );
}
