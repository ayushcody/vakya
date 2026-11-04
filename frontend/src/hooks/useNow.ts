"use client";

import { useSyncExternalStore } from "react";

let current = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  current = Date.now();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    current = Date.now();
    timer = setInterval(tick, 30_000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** The current time in ms, refreshed every 30 seconds. Reads the clock outside render so components stay pure; 0 on the server. */
export function useNow(): number {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => 0,
  );
}
