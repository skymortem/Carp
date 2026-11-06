/* Carp — минимальный service worker.
 *
 * Задача не «работать офлайн» (данные живые и требуют сервера), а:
 *  - мгновенно отдавать статику (иконки, offline-заглушку) из кеша;
 *  - не показывать браузерную «не удалось открыть» при потере сети,
 *    а вернуть понятную заглушку.
 *
 * HTML и API намеренно НЕ кешируются: это личные данные в машине,
 * и на общем устройстве они не должны оставаться в кеше после выхода.
 */

const CACHE = 'carp-static-v1';
const PRECACHE = [
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/offline.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // CDN (Tailwind, Alpine, Chart.js) не трогаем — пусть кеширует браузер.
  if (url.origin !== self.location.origin) return;

  // Статика — из кеша, с обновлением в фоне.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Навигация: всегда сеть; при падении — заглушка, но не кеш страницы.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/static/offline.html'))
    );
  }
});
