/* Static-only offline teaching shell. Never cache APIs, auth, seller data, recordings or live receipts. */
const version=new URL(self.location.href).searchParams.get('v')||'3.1.0';
const cacheName='vani-public-shell-'+version;
const base=new URL(self.registration.scope).pathname.replace(/\/$/,'');
const modules=['app','core','lab','sync','speech','live','loading-tips','access','workshop','workshop-data','feedback','admin'];
const staticPaths=[...modules.map(name=>`${base}/static/${name}.js?v=${version}`),`${base}/static/styles.css?v=${version}`,`${base}/static/tokens.css?v=${version}`,`${base}/static/workshop-manifest.json?v=${version}`,`${base}/static/mark.svg`,`${base}/static/demo-media/demo-english.mp3?v=${version}`,`${base}/static/demo-media/demo-hindi.mp3?v=${version}`];
self.addEventListener('install',event=>event.waitUntil((async()=>{const cache=await caches.open(cacheName);await cache.addAll([base+'/',...staticPaths]);await self.skipWaiting();})()));
self.addEventListener('activate',event=>event.waitUntil((async()=>{for(const name of await caches.keys())if(name.startsWith('vani-public-shell-')&&name!==cacheName)await caches.delete(name);await self.clients.claim();})()));
self.addEventListener('fetch',event=>{
 const request=event.request,url=new URL(request.url);if(request.method!=='GET'||url.origin!==self.location.origin)return;
 if(url.pathname.startsWith(base+'/api/')||url.pathname.startsWith(base+'/docs')||url.pathname.endsWith('/openapi.json'))return;
 if(request.mode==='navigate'&&[base+'/',base,base+'/pm',base+'/pm/',base+'/walkthrough',base+'/walkthrough/'].includes(url.pathname)){
  event.respondWith(fetch(request).catch(async()=>{const cached=await caches.match(base+'/');return cached||Response.error();}));return;
 }
 if(staticPaths.some(path=>new URL(path,self.location.origin).href===url.href))event.respondWith((async()=>{const cached=await caches.match(request);if(cached)return cached;const response=await fetch(request);if(response.ok)(await caches.open(cacheName)).put(request,response.clone());return response;})());
});
