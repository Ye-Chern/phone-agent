const CACHE = "phoneagent-v6";
const ASSETS = ["/web/index.html", "/web/enhancements.js", "/web/markdown.js", "/web/upgrade.css", "/web/vendor/marked.umd.js", "/web/vendor/purify.min.js", "/web/vendor/highlight.min.js", "/web/vendor/katex.min.js", "/web/vendor/katex.min.css", "/web/vendor/auto-render.min.js", "/web/manifest.webmanifest", "/web/icon-192.png", "/web/icon-512.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  // Keep the UI fresh when connected. Cached files are only an offline fallback.
  event.respondWith(fetch(request).then(response => {
    if (response.ok && url.pathname.startsWith("/web/")) {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(request, copy));
    }
    return response;
  }).catch(async () => {
    const cached = await caches.match(request) ||
      (request.mode === "navigate" ? await caches.match("/web/index.html") : null);
    return cached || new Response("Offline", {status: 503, statusText: "Offline"});
  }));
});
