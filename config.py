"""Settings (environment variables) and small shared helpers."""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('AGRIPILOT_DB') or ROOT / 'agripilot.db')
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', '8765'))

# Seconds between checkpoints, exactly as in the spec. AGRIPILOT_SPEED=10 runs the demo 10x faster.
SPEED = float(os.getenv('AGRIPILOT_SPEED', '1'))
DELAYS = {
    'card': 10,        # order message  -> approval card
    'scheduled': 15,   # Approve        -> "transport & labour scheduled"
    'delivered': 20,   # scheduled      -> "delivered to buyer"
    'completed': 15,   # delivered      -> "transaction completed"
}

# Optional: protect the owner app. Open https://<site>/?key=<OWNER_KEY> once on the phone.
OWNER_KEY = os.getenv('OWNER_KEY', '')

BUYER_DESTINATION = os.getenv('BUYER_DESTINATION', 'Rajasthan')
STARTING_STOCK = int(os.getenv('STARTING_STOCK', '0'))


def delay(step):
    return DELAYS[step] / SPEED


def now():
    return time.time()


def grp(n):
    """Indian digit grouping: 1234567 -> 12,34,567."""
    s = str(int(round(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ','.join(parts + [tail])


def inr(value):
    """₹ amount with Indian grouping; paise shown only when present."""
    value = round(float(value), 2)
    sign = '-' if value < 0 else ''
    value = abs(value)
    paise = round((value - int(value)) * 100)
    return '%s₹%s%s' % (sign, grp(int(value)), '.%02d' % paise if paise else '')


def rate(value):
    """Per-nut price: 18 -> '₹18', 9.9 -> '₹9.9'."""
    value = round(float(value), 2)
    return '₹' + (str(int(value)) if value == int(value) else ('%.2f' % value).rstrip('0'))
