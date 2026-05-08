const CACHE_NAME = 'chess-analyzer-shell-v2';
const SHELL_ASSETS = [
  '/',
  '/manifest.json',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/chesspieces/wK.png',
  '/static/img/chesspieces/wQ.png',
  '/static/img/chesspieces/wR.png',
  '/static/img/chesspieces/wB.png',
  '/static/img/chesspieces/wN.png',
  '/static/img/chesspieces/wP.png',
  '/static/img/chesspieces/bK.png',
  '/static/img/chesspieces/bQ.png',
  '/static/img/chesspieces/bR.png',
  '/static/img/chesspieces/bB.png',
  '/static/img/chesspieces/bN.png',
  '/static/img/chesspieces/bP.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    ))
  );
  self.clients.claim();
});

// Network-first for app shell — serves fresh JS/HTML/CSS whenever the network is up,
// falls back to cache only when offline. Prevents stale UI after server-side changes.
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
