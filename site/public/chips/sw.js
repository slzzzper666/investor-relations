const CACHE = 'sanchou-2026-09-10';
const CORE = ['./','./index.html','./logo.svg','./manifest.webmanifest','./icon-192.png','./icon-512.png','./maskable-512.png','./apple-touch-icon.png','./foreign_futures.png','./retail_futures.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== location.origin) return;  // 字型等 CDN 交給瀏覽器
  e.respondWith(
    fetch(req).then(res => {
      const cp = res.clone();
      caches.open(CACHE).then(c => c.put(req, cp));
      return res;
    }).catch(() => caches.match(req).then(m => m || caches.match('./index.html')))
  );
});
