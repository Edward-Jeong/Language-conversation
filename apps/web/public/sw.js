const CACHE = 'language-conversation-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icon.svg']
self.addEventListener('install', (event) => event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL))))
self.addEventListener('activate', (event) => event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))))
self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return
  if (!['document', 'script', 'style', 'image', 'font', 'manifest'].includes(request.destination)) return
  event.respondWith(fetch(request).then((response) => {
    if (response.ok) caches.open(CACHE).then((cache) => cache.put(request, response.clone()))
    return response
  }).catch(() => caches.match(request).then((cached) => cached || caches.match('/index.html'))))
})
