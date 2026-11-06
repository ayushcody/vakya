/*
 * Vakya desk service worker. It does one thing: show a booking alert and open the booking when it is tapped.
 * It caches nothing and does not handle fetches, so the desk always shows what the server has now.
 * Registered by src/lib/deskAlerts.ts when the owner turns alerts on. Contract: docs/DESK_FEATURES.md.
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/desk";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if (new URL(w.url).origin === self.location.origin && "focus" in w) {
          w.navigate(url);
          return w.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
