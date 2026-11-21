// Primero la red: los cambios llegan solos. Solo se guarda la cáscara (nunca datos de /api/).
const CACHE = "tutor-panel-v1";
const CASCARA = ["./", "index.html", "estilo.css", "app.js", "icono.svg", "manifest.json"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CASCARA))); self.skipWaiting(); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    const copia = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copia));
    return r;
  }).catch(() => caches.match(e.request)));
});
