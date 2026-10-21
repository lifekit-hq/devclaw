// Serves the production build the way the backend does (devclaw/server/routes/console.py): `/`
// redirects to /console, and /console/* answers static files with every unknown extensionless
// path answered by index.html (the SPA fallback). Used only by the Playwright webServer, so the
// e2e tests run against the built bundle rather than the dev server.
//
// The feeds are answered from fixtures/ (feeds.json maps each feed path to its fixture), so a
// browser the tests do not drive (the impeccable URL scan's) renders the same pages the specs
// see. `THEME` sets the account's saved theme (default `system`).
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {extname, join, normalize} from 'node:path';

const root = join(import.meta.dirname, '..', '..', 'devclaw', 'server', 'console_dist');
const fixtures = join(import.meta.dirname, 'fixtures');
const feeds = JSON.parse(await readFile(join(fixtures, 'feeds.json'), 'utf8'));
const preferences = JSON.stringify({appearance: {theme: process.env.THEME ?? 'system'}});
const port = Number(process.env.PORT ?? 4173);
const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://x').pathname;
  if (pathname in feeds) {
    const body = await readFile(join(fixtures, `${feeds[pathname]}.json`));
    res.writeHead(200, {'content-type': types['.json']}).end(body);
    return;
  }
  if (pathname === '/api/me/preferences') {
    res.writeHead(200, {'content-type': types['.json']}).end(preferences);
    return;
  }
  if (pathname === '/') {
    res.writeHead(307, {location: '/console'}).end();
    return;
  }
  if (pathname !== '/console' && !pathname.startsWith('/console/')) {
    res.writeHead(404).end();
    return;
  }
  const path = normalize(decodeURIComponent(pathname.slice('/console'.length) || '/'));
  const file = extname(path) ? join(root, path) : join(root, 'index.html');
  try {
    const body = await readFile(file.startsWith(root) ? file : join(root, 'index.html'));
    res.writeHead(200, {'content-type': types[extname(file)] ?? 'application/octet-stream'});
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(port, '127.0.0.1');
