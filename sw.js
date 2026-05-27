/* ============================================================
 *  ywy0.github.io — Service Worker
 *  Makes the blog work offline by caching CDN & same-origin
 *  resources using a cache-first strategy.
 * ============================================================ */

const CACHE_NAME = 'ywy-blog-v1';

/* jsdelivr domains used by this site (matches the auto-fallback list) */
const CDN_DOMAINS = [
  'cdn.jsdelivr.net',
  'fastly.jsdelivr.net',
  'gcore.jsdelivr.net',
  'cdn.zenless.top',
  'testingcf.jsdelivr.net',
  'test1.jsdelivr.net',
];

/* ---- Install: pre-cache critical assets ---- */
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll([
        /* theme + font CSS (will be fulfilled from network and cached) */
        /* dynamic CDN resources are cached on first fetch instead */
      ]);
    }),
  );
});

/* ---- Activate: clean old caches ---- */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key)),
      ),
    ),
  );
  /* take control of all clients immediately */
  self.clients.claim();
});

/* ---- Fetch: decide strategy per request ---- */
self.addEventListener('fetch', (event) => {
  const { request } = event;

  /* only cache GET requests */
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  /* never cache analytics */
  if (url.hostname === 'busuanzi.ibruce.info') return;

  /* CDN assets → cache-first (fast offline reads) */
  if (isCDN(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  /* same-origin navigations → network-first (fresh content, fallback to cache) */
  if (request.mode === 'navigate' && url.origin === location.origin) {
    event.respondWith(networkFirst(request));
    return;
  }

  /* same-origin static files → stale-while-revalidate */
  if (url.origin === location.origin) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }
});

/* ---- Helpers ---- */

function isCDN(url) {
  return CDN_DOMAINS.some((domain) => url.hostname === domain);
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response(
      '<!-- offline: resource not available -->',
      { status: 503, headers: { 'Content-Type': 'text/plain' } },
    );
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    /* last resort: serve the home page */
    return caches.match('/');
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);

  return cached || fetchPromise;
}
