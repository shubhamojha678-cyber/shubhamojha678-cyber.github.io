/* Service worker of the Rajasthan High Court cause-list search. Scope: this folder only.
   Lists, status and pages are ALWAYS asked from the network first. A saved copy is used only when
   the network fails, and it is then marked with the time it was saved (header x-sw-saved) so the
   page says "offline - showing the copy saved at ...". Only versioned assets (app.js?v=, app.css?v=)
   and the icons are served cache-first; a new version is a new URL, so nobody is pinned to old code. */
var VER = '3523fb44c3c5', STATIC = 'rhc-static-' + VER, KEEP = 'rhc-keep-1';
var SCOPE = self.registration.scope, BASE = new URL(SCOPE).pathname;
var ASSETS = ["app.js?v=9cc46c1c26", "app.css?v=c7468bbbf8", "icon-192.png"];

function isAsset(rel, search) { return /^icon-[\w-]+\.png$/.test(rel) || (/^app\.(js|css)$/.test(rel) && search.indexOf('?v=') === 0); }
function dropOld(c, url) {               // one copy per path: an older ?v= of the same file goes
  return c.keys(url.origin + url.pathname, { ignoreSearch: true }).then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k.url !== url.href; }).map(function (k) { return c.delete(k); }));
  });
}
function keep(url, resp) {               // save a good network answer, stamped with the time
  return caches.open(KEEP).then(function (c) {
    return resp.blob().then(function (body) {
      var h = new Headers(resp.headers);
      h.delete('content-encoding'); h.delete('content-length');
      h.set('x-sw-saved', String(Date.now()));
      return dropOld(c, url).then(function () { return c.put(url.href, new Response(body, { status: 200, headers: h })); }).then(function () {
        if (!/data\/meta\.json$/.test(url.pathname)) return;
        return body.text().then(function (t) {           // day files that left the site are dropped
          var want = JSON.parse(t).site_dates || [];
          return c.keys().then(function (ks) {
            return Promise.all(ks.map(function (k) {
              var m = /\/data\/d\/cl_(\d{4}-\d\d-\d\d)\.json/.exec(k.url);
              return m && want.indexOf(m[1]) < 0 ? c.delete(k) : null;
            }));
          });
        });
      });
    });
  }).catch(function () {});
}
function saved(req, url) {
  return caches.open(KEEP).then(function (c) {
    return c.match(url.href).then(function (r) { return r || c.match(url.origin + url.pathname, { ignoreSearch: true }); });
  });
}
function netFirst(ev, url) {
  // a page is always re-checked with the server (not taken from the browser's 10-minute cache), so the
  // first visit after a deploy already gets the new page and, through its ?v= links, the new script
  return (ev.request.mode === 'navigate' ? fetch(ev.request, { cache: 'no-cache' }) : fetch(ev.request)).then(function (r) {
    if (r && r.ok && r.type === 'basic') { ev.waitUntil(keep(url, r.clone())); return r; }
    if (r && r.status >= 500) return saved(ev.request, url).then(function (c) { return c || r; });
    return r;
  }, function (err) {
    return saved(ev.request, url).then(function (c) { if (c) return c; throw err; });
  });
}
function cacheFirst(ev, url) {
  return caches.open(STATIC).then(function (c) {
    return c.match(url.href).then(function (hit) {
      return hit || fetch(ev.request).then(function (r) {
        if (r && r.ok && r.type === 'basic') { var copy = r.clone(); ev.waitUntil(dropOld(c, url).then(function () { return c.put(url.href, copy); })); }
        return r;
      });
    });
  });
}
self.addEventListener('install', function (ev) {
  self.skipWaiting();
  ev.waitUntil(caches.open(STATIC).then(function (c) {
    return Promise.all(ASSETS.map(function (u) {
      return fetch(SCOPE + u).then(function (r) { if (r.ok) return c.put(SCOPE + u, r); }).catch(function () {});
    }));
  }));
});
self.addEventListener('activate', function (ev) {
  ev.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k.indexOf('rhc-') === 0 && k !== STATIC && k !== KEEP; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (ev) {
  var req = ev.request, url = new URL(req.url);
  if (req.method !== 'GET' || req.url.indexOf(SCOPE) !== 0) return;      // nothing outside this folder
  var rel = url.pathname.slice(BASE.length);
  if (rel === 'sw.js') return;
  ev.respondWith(isAsset(rel, url.search) ? cacheFirst(ev, url) : netFirst(ev, url));
});
self.addEventListener('message', function (ev) {      // the page names what it has just loaded: keep a copy for offline use
  var list = ev.data && ev.data.warm;
  if (!list || !list.length) return;
  ev.waitUntil(caches.open(KEEP).then(function (c) {
    return list.slice(0, 12).reduce(function (p, u) {
      return p.then(function () {
        var url = new URL(u, SCOPE);
        if (url.href.indexOf(SCOPE) !== 0) return;
        return c.match(url.href).then(function (hit) {
          if (hit && url.search) return;                 // a versioned file we already hold
          return fetch(url.href, url.search ? {} : { cache: 'no-cache' }).then(function (r) { if (r.ok && r.type === 'basic') return keep(url, r); }).catch(function () {});
        });
      });
    }, Promise.resolve());
  }));
});
