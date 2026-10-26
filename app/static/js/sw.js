// Service worker: только кэш статики сайта (css, js, шрифты, картинки, стикеры), без офлайн-режима.
// Страницы, ответы API и загруженные файлы (/media/ — фото детей) не кэшируются никогда.
// Отдаётся приложением по адресу /sw.js, чтобы управлять всем сайтом (scope "/").
const CACHE = "est-static-v1";
const MAX_ENTRIES = 120; // у статики в адресе ?v=…: после обновления сайта старые копии вытесняются

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cache) {
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES)).map((k) => cache.delete(k)));
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    await cache.put(request, response.clone());
    trim(cache);
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith("/static/")) {
    return; // всё остальное — как без service worker
  }
  event.respondWith(cacheFirst(event.request));
});
