// Service Worker: hält die App-Shell offline verfügbar.
// API-Anfragen gehen immer ins Netz (Daten liegen in IndexedDB, nicht im SW-Cache).
const VERSION = "__VERSION__";
const CACHE = "rcb-shell-" + VERSION;
const ASSETS = __ASSETS__;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS.map((url) => new Request(url, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((namen) =>
        Promise.all(namen.filter((n) => n.startsWith("rcb-shell-") && n !== CACHE).map((n) => caches.delete(n))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const anfrage = event.request;
  const url = new URL(anfrage.url);
  if (anfrage.method !== "GET" || url.origin !== self.location.origin) return;
  // Nur die App-Shell (Geräteseiten, Manifeste, statische Dateien). Admin, Ergebnisse und API
  // gehen immer ins Netz und werden nie zwischengespeichert.
  if (!ASSETS.includes(url.pathname) && !url.pathname.startsWith("/static/")) return; // inkl. /hilfe/…

  // Stale-while-revalidate: sofort aus dem Cache (auch bei schlechtem Netz), im Hintergrund aktualisieren.
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const schluessel = url.origin + url.pathname;
      const gecacht = await cache.match(schluessel);
      const ausNetz = fetch(anfrage)
        .then((antwort) => {
          if (antwort.ok) cache.put(schluessel, antwort.clone());
          return antwort;
        })
        .catch(() => null);
      if (gecacht) {
        event.waitUntil(ausNetz);
        return gecacht;
      }
      return (await ausNetz) || new Response("Offline und nicht im Cache", { status: 503 });
    })(),
  );
});
