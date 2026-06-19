import http from 'node:http';
import { URL } from 'node:url';

const targetOrigin = (process.env.PYTHON_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const port = Number(process.env.PORT || 3000);

function allowCors(req, res) {
  const origin = req.headers.origin || '*';
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-CSRFToken, X-Requested-With, Accept, Authorization');
  res.setHeader('Vary', 'Origin');
}

function copyHeaders(source, res) {
  for (const [key, value] of source.entries()) {
    const lower = key.toLowerCase();
    if (['access-control-allow-origin', 'access-control-allow-credentials', 'access-control-allow-methods', 'access-control-allow-headers', 'vary', 'content-length'].includes(lower)) {
      continue;
    }
    if (lower === 'set-cookie') {
      res.setHeader('Set-Cookie', value);
      continue;
    }
    res.setHeader(key, value);
  }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function proxy(req, res) {
  const requestUrl = new URL(req.url || '/', targetOrigin);
  const method = req.method || 'GET';

  if (method === 'OPTIONS') {
    allowCors(req, res);
    res.statusCode = 204;
    res.end();
    return;
  }

  if (requestUrl.pathname === '/healthz') {
    allowCors(req, res);
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify({ ok: true, target: targetOrigin }));
    return;
  }

  const headers = { ...req.headers };
  delete headers.host;
  delete headers.connection;
  delete headers['content-length'];
  headers['x-forwarded-host'] = req.headers.host || '';
  headers['x-forwarded-proto'] = 'https';
  if (req.headers.origin) {
    headers.origin = req.headers.origin;
  }

  let body;
  if (!['GET', 'HEAD'].includes(method)) {
    body = await readBody(req);
  }

  const upstream = await fetch(requestUrl, {
    method,
    headers,
    body,
    redirect: 'manual',
  });

  allowCors(req, res);
  res.statusCode = upstream.status;
  copyHeaders(upstream.headers, res);

  const buffer = Buffer.from(await upstream.arrayBuffer());
  if (!res.getHeader('Content-Type')) {
    res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/octet-stream');
  }
  res.end(buffer);
}

http.createServer((req, res) => {
  proxy(req, res).catch((error) => {
    res.statusCode = 502;
    allowCors(req, res);
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify({ error: 'Proxy error', detail: String(error?.message || error) }));
  });
}).listen(port, () => {
  console.log(`Backend proxy listening on ${port}, forwarding to ${targetOrigin}`);
});
