// Serves the production build (dist/) the way the backend does: static files, with every unknown
// extensionless path answered by index.html (the SPA fallback). Used only by the Playwright
// webServer, so the e2e tests run against the built bundle rather than the dev server.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {extname, join, normalize} from 'node:path';

const root = join(import.meta.dirname, '..', 'dist');
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
};

createServer(async (req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname));
  const file = extname(path) ? join(root, path) : join(root, 'index.html');
  try {
    const body = await readFile(file.startsWith(root) ? file : join(root, 'index.html'));
    res.writeHead(200, {'content-type': types[extname(file)] ?? 'application/octet-stream'});
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(port, '127.0.0.1');
