"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { cn } from "@/lib/utils";

let playing: HTMLAudioElement | null = null;

/** One small play/pause button for one clip. Only one clip plays at a time. The clip is fetched when it is first played. */
export function PlayButton({ src, label, className }: { src: string; label: string; className?: string }) {
  const el = useRef<HTMLAudioElement | null>(null);
  const [on, setOn] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    return () => {
      if (el.current) {
        el.current.pause();
        if (playing === el.current) playing = null;
      }
    };
  }, []);

  const toggle = () => {
    if (!el.current) {
      const a = new Audio();
      a.preload = "none";
      a.src = src;
      a.onended = () => setOn(false);
      a.onpause = () => setOn(false);
      a.onplay = () => setOn(true);
      a.onerror = () => {
        setFailed(true);
        setOn(false);
      };
      el.current = a;
    }
    const a = el.current;
    if (!a.paused) {
      a.pause();
      return;
    }
    if (playing && playing !== a) playing.pause();
    playing = a;
    void a.play().catch(() => setFailed(true));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={on}
      aria-label={failed ? `${label} (could not be played)` : `${on ? "Pause" : "Play"} ${label}`}
      disabled={failed}
      className={cn(
        "inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border bg-bg-base px-3 text-xs font-medium text-text-primary",
        "transition-colors hover:border-accent-primary hover:text-accent-primary disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
    >
      {on ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
      {failed ? "Not available" : label}
    </button>
  );
}
