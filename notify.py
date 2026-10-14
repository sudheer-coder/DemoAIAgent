"""Checkpoint notifications: stored for the app's activity list and sent as Web Push to the owner's phone."""
import base64
import json
import threading

from . import config, db


def send(txn_id, kind, title, body, push=True):
    """Record a notification; push it to every subscribed device unless push=False."""
    cur = db.execute('INSERT INTO notifications(txn,kind,title,body,created) VALUES(?,?,?,?,?)',
                     (txn_id, kind, title, body, config.now()))
    nid = cur.lastrowid
    if push:
        _push_async({'title': title, 'body': body, 'tag': 'n%d' % nid, 'url': '/'})
    return nid


# ---- Web Push ---------------------------------------------------------------------
def vapid_keys():
    row = db.one('SELECT value FROM settings WHERE key=?', ('vapid',))
    if row:
        return json.loads(row['value'])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',
               ('vapid', json.dumps({'private': pem, 'public': pub})))
    return json.loads(db.one('SELECT value FROM settings WHERE key=?', ('vapid',))['value'])


def push_available():
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def subscribe(sub):
    keys = (sub or {}).get('keys') or {}
    if not str(sub.get('endpoint', '')).startswith('https://') or not keys.get('p256dh') or not keys.get('auth'):
        raise ValueError('Invalid push subscription')
    db.execute('INSERT OR REPLACE INTO push_subscriptions(endpoint,p256dh,auth) VALUES(?,?,?)',
               (sub['endpoint'], keys['p256dh'], keys['auth']))


def _push_async(payload):
    subs = db.rows('SELECT * FROM push_subscriptions')
    if not subs or not push_available():
        return
    threading.Thread(target=_push_all, args=(subs, payload), daemon=True).start()


def _push_all(subs, payload):
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush
    vapid = Vapid02.from_pem(vapid_keys()['private'].encode())
    for sub in subs:
        try:
            webpush({'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']}},
                    json.dumps(payload), vapid_private_key=vapid,
                    vapid_claims={'sub': 'mailto:owner@agripilot.app'}, ttl=86400)
        except WebPushException as ex:
            if ex.response is not None and ex.response.status_code in (404, 410):
                db.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (sub['endpoint'],))
            else:
                print('push failed:', ex, flush=True)
        except Exception as ex:  # a dead device must never stop the deal
            print('push failed:', ex, flush=True)
