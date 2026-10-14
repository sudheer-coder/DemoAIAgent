"""HTTP routes. The owner app only ever reads state and posts Approve / Deny."""
import hmac
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from . import config, notify, supervisor

STATIC = {
    '/sw.js': 'text/javascript', '/manifest.webmanifest': 'application/manifest+json',
    '/icon.svg': 'image/svg+xml', '/icon-192.png': 'image/png', '/icon-512.png': 'image/png',
}
COOKIE = 'agp_owner'


class Handler(BaseHTTPRequestHandler):
    # ---- responses ----
    def reply(self, code, body, kind='application/json', headers=()):
        data = body if isinstance(body, bytes) else (json.dumps(body, default=str) if kind == 'application/json' else body).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind + ('; charset=utf-8' if kind.startswith('text/html') else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def page(self, name, headers=()):
        self.reply(200, (config.ROOT / name).read_bytes(), 'text/html', headers)

    def body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if not 0 < n <= 20000:
            raise ValueError('Invalid request')
        return json.loads(self.rfile.read(n))

    # ---- optional owner key ----
    def owner_ok(self):
        if not config.OWNER_KEY:
            return True
        for part in self.headers.get('Cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE and hmac.compare_digest(v, config.OWNER_KEY):
                return True
        return False

    # ---- routes ----
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        try:
            if path == '/api/health':
                return self.reply(200, {'healthy': True})
            if path in STATIC:
                return self.reply(200, (config.ROOT / path[1:]).read_bytes(), STATIC[path])
            key = parse_qs(url.query).get('key', [''])[0]
            if path in ('/', '/index.html', '/buyer', '/dashboard') and config.OWNER_KEY and key \
                    and hmac.compare_digest(key, config.OWNER_KEY):   # private link: remember this device, drop the key from the URL
                return self.reply(302, b'', 'text/plain', [
                    ('Location', path), ('Set-Cookie', '%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000' % (COOKIE, key))])
            if path in ('/buyer', '/dashboard'):
                return self.page('buyer.html')
            if path in ('/', '/index.html'):
                if not self.owner_ok():
                    return self.reply(401, '<p style="font-family:sans-serif;padding:30px">Open the private link you were given.</p>', 'text/html')
                return self.page('index.html')
            if not self.owner_ok():
                return self.reply(401, {'error': 'owner link required'})
            if path == '/api/state':
                return self.reply(200, supervisor.app_state())
            if path == '/api/board':
                return self.reply(200, supervisor.board())
            if path == '/api/push/key':
                ok = notify.push_available()
                return self.reply(200, {'available': ok, 'key': notify.vapid_keys()['public'] if ok else ''})
            self.reply(404, {'error': 'not found'})
        except Exception as ex:
            self.reply(500, {'error': str(ex)})

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            data = self.body()
            if path == '/api/order':      # the buyer's message channel (no owner key: buyers are outside)
                return self.reply(200, supervisor.receive_order(data.get('text'), data.get('buyer') or 'Buyer'))
            if not self.owner_ok():
                return self.reply(401, {'error': 'owner link required'})
            if path == '/api/decision':
                t = supervisor.decide(str(data.get('id', '')), str(data.get('decision', '')))
                return self.reply(200, {'id': t['id'], 'state': t['state']})
            if path == '/api/retry':
                t = supervisor.retry(str(data.get('id', '')))
                return self.reply(200, {'id': t['id'], 'state': t['state']})
            if path == '/api/push/subscribe':
                notify.subscribe(data.get('subscription') or {})
                return self.reply(200, {'ok': True})
            self.reply(404, {'error': 'not found'})
        except ValueError as ex:
            self.reply(400, {'error': str(ex)})
        except Exception as ex:
            self.reply(500, {'error': str(ex)})

    def log_message(self, *args):
        pass
