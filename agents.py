"""The agents inside the "Agentic product" box, plus the live data feed.

  Middlemen        the human layer: the buyer's message comes in through here
  Smart Price      prices the deal from the live feed + the order
  Data Warehouse   the memory: stock and deal records
  Co-ordination    runs the four physical parties: quality check, seller (farmer), labour, transport
  Supervisor       lives in supervisor.py (it routes work between all of these)

Everything the field agents "do" (message farmers, book the lorry, assign the crew) is recorded on the
transaction, but nothing leaves this server: there is no real messaging, booking or payment behind it yet.
Each method marked SEAM is where a real integration would plug in.
"""
import math
import os
import re

from . import config, db


# ---------------------------------------------------------------- live data feed
class LiveFeed:
    """Real-time costs flowing into the Smart Price Agent.

    SEAM: replace snapshot() with real sources (diesel price, daily wage, toll plan, farm-gate rate).
    Until then these are fixed demo values (override with FEED_* environment variables) chosen so the
    spec's example order prices exactly as drawn: 10,000 nuts -> 99,000 / 8,000 / 5,000 / 2,000.
    """

    @staticmethod
    def snapshot():
        env = lambda name, default: float(os.getenv('FEED_' + name, default))
        return {
            'farmer_rate': env('FARMER_RATE', 9.90),        # Rs per nut paid to farmers
            'transport_per_nut': env('TRANSPORT', 0.80),    # fuel-linked lorry cost per nut
            'labour_per_nut': env('LABOUR', 0.50),          # loading wages per nut
            'toll_per_nut': env('TOLL', 0.20),              # tolls per nut (route average)
            'source': 'demo',
        }


# ---------------------------------------------------------------- Middlemen
class MiddlemenAgent:
    """Reads the buyer's plain message: 'Need 10,000 coconuts, 18 rupees per coconut'."""

    QTY = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?)?\s*(?:coconuts?|nuts?|pieces?|pcs)\b')
    PRICE = (
        re.compile(r'(?:₹|\brs\.?|\brupees?|\binr)\s*(\d+(?:\.\d+)?)'),
        re.compile(r'(\d+(?:\.\d+)?)\s*(?:₹|\brs\b\.?|\brupees?|\binr\b|per\s+(?:coconut|nut|piece)|/\s*(?:coconut|nut|piece))'),
    )

    @classmethod
    def parse(cls, text):
        t = ' '.join(str(text or '').lower().split())
        m = cls.QTY.search(t)
        if not m:
            raise ValueError("I couldn't find the quantity. Try: Need 10,000 coconuts, 18 rupees per coconut")
        qty = float(m.group(1).replace(',', ''))
        qty *= {'k': 1e3, 'thousand': 1e3}.get(m.group(2) or '', 1e5 if m.group(2) else 1)
        price = None
        for pattern in cls.PRICE:
            p = pattern.search(t)
            if p:
                price = float(p.group(1))
                break
        if price is None:
            raise ValueError("I couldn't find the price. Try: Need 10,000 coconuts, 18 rupees per coconut")
        qty = int(qty)
        if not 1 <= qty <= 200000:
            raise ValueError('Quantity must be between 1 and 2,00,000 coconuts')
        if not 0.5 <= price <= 1000:
            raise ValueError('Price must be between ₹0.50 and ₹1,000 per coconut')
        return qty, price


# ---------------------------------------------------------------- Data Warehouse
class DataWarehouseAgent:
    """Warehouse stock and deal records. Stock is reserved when a card is shown and consumed at settlement."""

    @staticmethod
    def snapshot():
        w = db.one('SELECT stock,reserved,cost FROM warehouse WHERE id=1')
        return {'stock': w['stock'], 'reserved': w['reserved'], 'available': w['stock'] - w['reserved'], 'cost': w['cost']}

    @staticmethod
    def reserve(n):
        db.execute('UPDATE warehouse SET reserved=reserved+? WHERE id=1', (n,))

    @staticmethod
    def release(n):
        db.execute('UPDATE warehouse SET reserved=MAX(0,reserved-?) WHERE id=1', (n,))

    @classmethod
    def consume(cls, n):
        db.execute('UPDATE warehouse SET stock=MAX(0,stock-?), reserved=MAX(0,reserved-?) WHERE id=1', (n, n))

    @staticmethod
    def restock(n, cost_per_nut):
        """Add stock at a cost; keeps a weighted-average cost per nut."""
        w = db.one('SELECT stock,cost FROM warehouse WHERE id=1')
        total = w['stock'] + n
        cost = (w['stock'] * w['cost'] + n * cost_per_nut) / total if total else cost_per_nut
        db.execute('UPDATE warehouse SET stock=?, cost=? WHERE id=1', (total, cost))


# ---------------------------------------------------------------- Smart Price
class SmartPriceAgent:
    @staticmethod
    def price(qty, offer, warehouse, feed):
        """Buyer's order + live feed + warehouse stock -> the priced deal."""
        from_stock = min(qty, warehouse['available'])
        to_source = qty - from_stock
        purchase = round(to_source * feed['farmer_rate'], 2)
        stock_cost = round(from_stock * warehouse['cost'], 2)
        transport = round(qty * feed['transport_per_nut'], 2)
        labour = round(qty * feed['labour_per_nut'], 2)
        tolls = round(qty * feed['toll_per_nut'], 2)
        total = round(purchase + stock_cost + transport + labour + tolls, 2)
        revenue = round(qty * offer, 2)
        return {
            'qty': qty, 'offer': offer,
            'stock': warehouse['available'], 'from_stock': from_stock, 'to_source': to_source,
            'farmer_rate': feed['farmer_rate'], 'purchase': purchase, 'stock_cost': stock_cost,
            'transport': transport, 'labour': labour, 'tolls': tolls,
            'total_cost': total, 'revenue': revenue, 'profit': round(revenue - total, 2),
            'feed_source': feed['source'],
        }


# ---------------------------------------------------------------- Co-ordination
class CoordinationAgent:
    """Runs the four physical parties once a deal is approved.

    SEAM: the roster below is demo data. Real farmer messaging, lorry booking, crew assignment and
    quality-grader routing would replace the bodies of schedule() and deliver().
    """

    FARMERS = [  # (name, pickup spot, share of the order)
        ('Ravi Farms', 'Main Road', 0.40),
        ('Lakshmi Growers', 'Canal Street', 0.35),
        ('Palm Collective', 'Canal Street', 0.25),
    ]
    TOWN = 'Ambajipet'
    FLEET = ['AP-05-X-4421', 'AP-16-T-0872', 'AP-07-K-3310']
    NUTS_PER_CREW_MEMBER = 1700

    @classmethod
    def schedule(cls, txn):
        """After Approve: farmers, lorry, crew, timers, quality-check routing."""
        plan = txn['plan']
        shortage = plan['to_source']
        farmers, left = [], shortage
        for i, (name, spot, share) in enumerate(cls.FARMERS):
            q = left if i == len(cls.FARMERS) - 1 else int(round(shortage * share))
            q = min(q, left)
            left -= q
            if q > 0:
                farmers.append({'name': name, 'spot': spot, 'qty': q, 'rate': plan['farmer_rate'],
                                'amount': round(q * plan['farmer_rate'], 2)})
        spots = []
        for f in farmers:
            if f['spot'] not in spots:
                spots.append(f['spot'])
        crew = max(2, int(round(plan['qty'] / cls.NUTS_PER_CREW_MEMBER)))
        minutes = int(round(plan['qty'] * 0.009 / 5.0)) * 5   # 10,000 nuts -> 90 min
        return {
            'lorry': cls.FLEET[(txn['seq'] - 1) % len(cls.FLEET)],
            'farmers': farmers,
            'pickups': (' & '.join(spots) + ', ' + cls.TOWN) if spots else None,
            'crew': crew, 'timer_minutes': minutes, 'bay': 'godown bay',
            'quality_check': 'routed to grader',
            'actions': (['messaged %d farmer(s)' % len(farmers)] if farmers else []) +
                       ['booked lorry', 'assigned crew of %d' % crew, 'set loading timer', 'started quality-check routing'],
        }

    @classmethod
    def deliver(cls, txn):
        """Quality check -> loading -> transport -> buyer confirmation, with dispatch paperwork."""
        seq = txn['seq']
        return {
            'quality': 'confirmed',
            'delivered_qty': txn['qty'],
            'eway_bill': 'EWB-%012d' % (351000000000 + seq * 7919),   # simulated number
            'gst_invoice': 'GST/%s' % txn['id'],
            'tolls_verified': True,
        }

    @staticmethod
    def clock(minutes):
        h, m = divmod(int(minutes), 60)
        return ('%dh %02dm' % (h, m) if m else '%dh' % h) if h else '%dm' % m
