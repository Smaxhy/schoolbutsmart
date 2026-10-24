// Network first for everything, so a new deploy shows up on the next load;
// the cache is only a fallback for when the phone is offline.
const CACHE = "artevelde-tasks-v7";
const SHELL = [
  "./",
  "css/style.css",
  "js/app.js",
  "js/claude.js",
  "manifest.json",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  // cache: "reload" skips the browser's HTTP cache, so we never store an outdated copy.
  const requests = SHELL.map((url) => new Request(url, { cache: "reload" }));
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(requests)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Task data is handled by the page itself (localStorage fallback).
  if (url.pathname.endsWith("/tasks.json")) return;

  const key = request.mode === "navigate" ? "./" : request;
  event.respondWith(
    fetch(request, { cache: "no-cache" })
      .then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(key, copy));
        }
        return resp;
      })
      .catch(() => caches.match(key, { ignoreSearch: true }).then((cached) => cached || Response.error()))
  );
});

// ---- Web Push: notifications for big tasks, sent by the GitHub build -------------------
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Artevelde Tasks", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Artevelde Tasks", {
      body: data.body || "",
      icon: "icons/icon-192.png",
      badge: "icons/icon-192.png",
      tag: data.tag || undefined,
      data: { url: data.url || self.registration.scope },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || self.registration.scope;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const open = wins.find((w) => w.url.startsWith(self.registration.scope));
      return open ? open.focus() : self.clients.openWindow(url);
    })
  );
});
