"""SQLite storage: one transaction record carries the whole deal."""
import json
import sqlite3
import threading

from . import config

LOCK = threading.RLock()
_conn = None

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS transactions(
        id TEXT PRIMARY KEY, seq INTEGER NOT NULL, buyer TEXT, destination TEXT,
        qty INTEGER NOT NULL, offer REAL NOT NULL,
        state TEXT NOT NULL,            -- created | in progress | goods completed | payment pending | settled | denied
        plan TEXT, coordination TEXT, payments TEXT, events TEXT,
        step TEXT, step_at REAL,        -- next automatic step and when it is due
        attention TEXT,                 -- set when the supervisor hit something unusual
        created REAL, updated REAL)""",
    """CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT, txn TEXT, kind TEXT, title TEXT, body TEXT, created REAL)""",
    "CREATE TABLE IF NOT EXISTS warehouse(id INTEGER PRIMARY KEY CHECK(id=1), stock INTEGER, reserved INTEGER, cost REAL)",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS push_subscriptions(endpoint TEXT PRIMARY KEY, p256dh TEXT, auth TEXT)",
]


def connect():
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def reset_connection():
    """Used by tests that point the app at a fresh database file."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


def execute(sql, params=()):
    with LOCK:
        return connect().execute(sql, params)


def rows(sql, params=()):
    with LOCK:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql, params=()):
    found = rows(sql, params)
    return found[0] if found else None


def init(feed_farmer_rate):
    for sql in SCHEMA:
        execute(sql)
    if not one('SELECT id FROM warehouse'):
        execute('INSERT INTO warehouse(id,stock,reserved,cost) VALUES(1,?,0,?)', (config.STARTING_STOCK, feed_farmer_rate))


# ---- transaction record helpers -------------------------------------------------
JSON_FIELDS = ('plan', 'coordination', 'payments', 'events')


def load(row):
    if row is None:
        return None
    for f in JSON_FIELDS:
        row[f] = json.loads(row[f]) if row.get(f) else ([] if f in ('payments', 'events') else None)
    return row


def get_txn(txn_id):
    return load(one('SELECT * FROM transactions WHERE id=?', (txn_id,)))


def save_txn(t):
    t['updated'] = config.now()
    execute(
        'UPDATE transactions SET state=?,plan=?,coordination=?,payments=?,events=?,step=?,step_at=?,attention=?,updated=? WHERE id=?',
        (t['state'], json.dumps(t['plan']), json.dumps(t['coordination']), json.dumps(t['payments']),
         json.dumps(t['events']), t['step'], t['step_at'], t['attention'], t['updated'], t['id']))


def set_state(t, state):
    t['state'] = state
    t['events'].append({'state': state, 'at': config.now()})
