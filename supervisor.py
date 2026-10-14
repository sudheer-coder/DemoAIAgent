"""Supervisor Agent: the boss in the middle. Routes work between the other agents and owns the flow.

    order message -> 10s -> approval card -> Approve -> 15s -> scheduled -> 20s -> delivered -> 15s -> completed

Exactly four checkpoints reach the owner: the approval card (the only decision) and three info-only
notifications. States: created -> in progress -> goods completed -> payment pending -> settled
(or denied). One transaction record carries the whole chain; every agent updates that same record.
"""
import json
import threading

from . import agents, config, db, notify
from .config import grp, inr, rate

LOCK = threading.RLock()
FINAL = ('settled', 'denied')


# ------------------------------------------------------------------ step 0: the order
def receive_order(text, buyer='Buyer'):
    """The buyer's plain message arrives through the Middlemen agent."""
    qty, offer = agents.MiddlemenAgent.parse(text)
    with LOCK:
        seq = (db.one('SELECT COALESCE(MAX(seq),0) AS n FROM transactions')['n']) + 1
        txn_id = 'T%d' % seq
        now = config.now()
        events = json.dumps([{'state': 'created', 'at': now}])
        db.execute(
            'INSERT INTO transactions(id,seq,buyer,destination,qty,offer,state,events,payments,step,step_at,created,updated) '
            "VALUES(?,?,?,?,?,?,'created',?,'[]','card',?,?,?)",
            (txn_id, seq, str(buyer)[:60] or 'Buyer', config.BUYER_DESTINATION, qty, offer, events,
             now + config.delay('card'), now, now))
    return {'id': txn_id, 'qty': qty, 'offer': offer}


# ------------------------------------------------------------------ the owner's only decision
def decide(txn_id, decision):
    with LOCK:
        t = db.get_txn(txn_id)
        if not t:
            raise ValueError('Unknown transaction')
        if t['state'] != 'created' or not t['plan'] or t['step']:
            raise ValueError('%s is not waiting for a decision' % txn_id)
        if decision == 'approve':
            db.set_state(t, 'in progress')
            t['coordination'] = agents.CoordinationAgent.schedule(t)      # farmers, lorry, crew, timers, QC routing
            t['step'], t['step_at'] = 'scheduled', config.now() + config.delay('scheduled')
        elif decision == 'deny':
            agents.DataWarehouseAgent.release(t['plan']['from_stock'])
            db.set_state(t, 'denied')
            t['step'] = t['step_at'] = None
            notify.send(txn_id, 'info', '%s — denied' % txn_id, 'Buyer will be told no. Nothing was booked.', push=False)
        else:
            raise ValueError('Decision must be approve or deny')
        db.save_txn(t)
        return t


def failed_step(t):
    """The step that stopped this deal, or None if nothing is wrong."""
    if not t['attention']:
        return None
    for e in reversed(t['events']):
        if e.get('state') == 'failed':
            return e.get('step')
    return 'card'


def retry(txn_id):
    """Owner taps Retry on a red item: clear the problem and run the stopped step again now."""
    with LOCK:
        t = db.get_txn(txn_id)
        if not t:
            raise ValueError('Unknown transaction')
        step = failed_step(t)
        if not step:
            raise ValueError('%s has nothing to retry' % txn_id)
        t['attention'] = None
        t['events'].append({'state': 'retried', 'step': step, 'at': config.now()})
        t['step'], t['step_at'] = step, config.now()
        db.save_txn(t)
        return t


# ------------------------------------------------------------------ the clock
def tick(now=None):
    """Run every step that is due. Called by the background worker (and directly by tests)."""
    now = config.now() if now is None else now
    for row in db.rows('SELECT id FROM transactions WHERE step IS NOT NULL AND step_at<=? ORDER BY step_at', (now,)):
        with LOCK:
            t = db.get_txn(row['id'])
            if not t or not t['step'] or t['step_at'] > now:
                continue
            step = t['step']
            try:
                STEPS[step](t, now)
            except Exception as ex:   # anything unusual: stop the deal, tell the owner
                t['attention'] = str(ex)
                t['events'].append({'state': 'failed', 'step': step, 'at': now})
                t['step'] = t['step_at'] = None
                notify.send(t['id'], 'info', '%s — needs your attention' % t['id'], str(ex))
            db.save_txn(t)


def worker(stop=None):
    import time
    while not (stop and stop.is_set()):
        try:
            tick()
        except Exception as ex:
            print('supervisor error:', ex, flush=True)
        time.sleep(0.25)


# ------------------------------------------------------------------ step 1: approval card
def _step_card(t, now):
    warehouse = agents.DataWarehouseAgent.snapshot()
    t['plan'] = agents.SmartPriceAgent.price(t['qty'], t['offer'], warehouse, agents.LiveFeed.snapshot())
    agents.DataWarehouseAgent.reserve(t['plan']['from_stock'])
    t['step'] = t['step_at'] = None                      # waits for the owner
    p = t['plan']
    notify.send(t['id'], 'card', 'New order %s — approve?' % t['id'],
                '%s coconuts @ %s/nut · estimated profit %s. Tap to review.' % (grp(t['qty']), rate(t['offer']), inr(p['profit'])))


# ------------------------------------------------------------------ step 2: scheduled
def _step_scheduled(t, now):
    c = t['coordination']
    if c['farmers']:
        lorry = '🚛 Lorry %s → %d farmer%s (%s)' % (c['lorry'], len(c['farmers']), '' if len(c['farmers']) == 1 else 's',
                                                    ' + '.join(grp(f['qty']) for f in c['farmers']))
    else:
        lorry = '🚛 Lorry %s → godown (all %s from stock)' % (c['lorry'], grp(t['qty']))
    lines = [lorry, '👷 Crew of %d → %s, timer %s' % (c['crew'], c['bay'], agents.CoordinationAgent.clock(c['timer_minutes']))]
    if c['pickups']:
        lines.append('📍 Pickups: ' + c['pickups'])
    notify.send(t['id'], 'scheduled', '%s — Transport & labour scheduled' % t['id'], '\n'.join(lines))
    t['step'], t['step_at'] = 'delivered', now + config.delay('delivered')


# ------------------------------------------------------------------ step 3: delivered
def _step_delivered(t, now):
    t['coordination']['delivery'] = agents.CoordinationAgent.deliver(t)
    db.set_state(t, 'goods completed')
    db.set_state(t, 'payment pending')
    notify.send(t['id'], 'delivered', '%s — Delivered to buyer' % t['id'],
                'All %s coconuts received in %s. Quality confirmed ✓' % (grp(t['qty']), t['destination']))
    t['step'], t['step_at'] = 'completed', now + config.delay('completed')


# ------------------------------------------------------------------ step 4: completed
def _step_completed(t, now):
    p, c = t['plan'], t['coordination']
    out = [{'to': f['name'], 'role': 'farmer', 'amount': f['amount']} for f in c['farmers']]
    out += [{'to': 'Lorry ' + c['lorry'], 'role': 'transport', 'amount': p['transport']},
            {'to': 'Loading crew', 'role': 'labour', 'amount': p['labour']},
            {'to': 'Toll plazas', 'role': 'tolls', 'amount': p['tolls']}]
    t['payments'] = out + [{'to': t['buyer'], 'role': 'buyer_receipt', 'amount': p['revenue']}]
    problem = verify_payments(t)
    if problem:
        raise ValueError('Payment check failed: ' + problem)
    agents.DataWarehouseAgent.consume(p['from_stock'])
    db.set_state(t, 'settled')
    t['step'] = t['step_at'] = None
    paid_out = round(sum(x['amount'] for x in out), 2)
    stock_left = agents.DataWarehouseAgent.snapshot()['stock']
    tail = '🏚️ Warehouse empty — restock before the next order' if stock_left == 0 else '🏚️ Warehouse: %s in stock' % grp(stock_left)
    notify.send(t['id'], 'completed', '%s — Transaction Completed ✅' % t['id'],
                'Paid out: %s | Received: %s\nProfit: %s\n%s' % (inr(paid_out), inr(p['revenue']), inr(p['profit']), tail))


def verify_payments(t):
    """Supervisor checks every payment against the deal record. Returns a problem text, or ''."""
    p = t['plan']
    paid_out = round(sum(x['amount'] for x in t['payments'] if x['role'] != 'buyer_receipt'), 2)
    expected = round(p['purchase'] + p['transport'] + p['labour'] + p['tolls'], 2)
    received = round(sum(x['amount'] for x in t['payments'] if x['role'] == 'buyer_receipt'), 2)
    if abs(paid_out - expected) > 0.05:
        return 'paid out %s but the deal says %s' % (inr(paid_out), inr(expected))
    if abs(received - p['revenue']) > 0.05:
        return 'received %s but the deal says %s' % (inr(received), inr(p['revenue']))
    return ''


STEPS = {'card': _step_card, 'scheduled': _step_scheduled, 'delivered': _step_delivered, 'completed': _step_completed}


# ------------------------------------------------------------------ what the app shows
def card_view(t):
    p = t['plan']
    if p['from_stock'] == 0:
        warehouse = '%s in stock ⚠️ — all %s to be sourced from farmers' % (grp(p['stock']), grp(p['qty']))
    elif p['to_source'] == 0:
        warehouse = '%s in stock ✓ — the whole order ships from the godown' % grp(p['stock'])
    else:
        warehouse = '%s in stock — %s from the godown, %s to be sourced from farmers' % (grp(p['stock']), grp(p['from_stock']), grp(p['to_source']))
    costs = [['Purchase', inr(p['purchase'])]]
    if p['stock_cost']:
        costs.append(['From stock', inr(p['stock_cost'])])
    costs += [['Transport', inr(p['transport'])], ['Labour', inr(p['labour'])], ['Tolls', inr(p['tolls'])]]
    return {'id': t['id'], 'title': '%s — %s coconuts @ %s/nut' % (t['id'], grp(t['qty']), rate(t['offer'])),
            'warehouse': warehouse, 'costs': costs, 'total_cost': inr(p['total_cost']), 'revenue': inr(p['revenue']),
            'profit': inr(p['profit']), 'loss': p['profit'] < 0}


def summary(t):
    return {'id': t['id'], 'qty': t['qty'], 'offer': t['offer'], 'state': t['state'], 'attention': t['attention'],
            'profit': t['plan']['profit'] if t['plan'] else None}


# ------------------------------------------------------------------ the dashboard
# Every column is one of: done (green) | progress (orange) | failed (red) | waiting (grey, not reached) | closed (grey)
PHASE = {'card': 'transaction', 'scheduled': 'transport', 'delivered': 'delivery', 'completed': 'delivery'}


def _cell(status, label, detail='', due=None):
    return {'status': status, 'label': label, 'detail': detail, 'due': due}


def board_row(t):
    p, c, step, state = t['plan'], t['coordination'] or {}, t['step'], t['state']
    broken = PHASE.get(failed_step(t))
    problem = t['attention'] or ''
    cols = {}

    # 1. Transaction: the buyer's order is read and priced
    if broken == 'transaction':
        cols['transaction'] = _cell('failed', 'Pricing failed', problem)
    elif not p:
        cols['transaction'] = _cell('progress', 'Pricing the order', 'Smart Price agent at work', t['step_at'])
    else:
        cols['transaction'] = _cell('done', 'Order priced', '%s for %s' % (inr(p['revenue']), t['buyer']))

    # 2. Price approval: the owner's one decision
    if not p:
        cols['approval'] = _cell('waiting', 'Not yet')
    elif state == 'denied':
        cols['approval'] = _cell('closed', 'Denied', 'Nothing was booked')
    elif state == 'created':
        cols['approval'] = _cell('progress', 'Awaiting owner', 'Est. profit %s' % inr(p['profit']))
    else:
        cols['approval'] = _cell('done', 'Approved', 'Est. profit %s' % inr(p['profit']))

    # 3. Transport: lorry, crew, farmers booked
    if broken == 'transport':
        cols['transport'] = _cell('failed', 'Booking failed', problem)
    elif state in ('created', 'denied'):
        cols['transport'] = _cell('waiting', 'Not yet') if state == 'created' else _cell('closed', 'Cancelled')
    elif step == 'scheduled':
        cols['transport'] = _cell('progress', 'Booking lorry & crew', 'Co-ordination agent at work', t['step_at'])
    else:
        cols['transport'] = _cell('done', 'Lorry ' + c.get('lorry', ''), 'Crew of %s · %d farmer(s)' % (c.get('crew', '?'), len(c.get('farmers', []))))

    # 4. Load delivered: on the road, received, paid
    dest = t['destination']
    if broken == 'delivery':
        cols['delivery'] = _cell('failed', 'Delivery / payment failed', problem)
    elif state in ('created', 'denied') or step == 'scheduled':
        cols['delivery'] = _cell('closed', 'Cancelled') if state == 'denied' else _cell('waiting', 'Not yet')
    elif step == 'delivered':
        cols['delivery'] = _cell('progress', 'On the road', 'Heading to %s' % dest, t['step_at'])
    elif step == 'completed':
        cols['delivery'] = _cell('progress', 'Delivered · settling payment', '%s nuts received in %s' % (grp(t['qty']), dest), t['step_at'])
    elif state == 'settled':
        cols['delivery'] = _cell('done', 'Delivered & paid', 'Profit %s' % inr(p['profit']))
    else:
        cols['delivery'] = _cell('waiting', 'Not yet')

    statuses = [x['status'] for x in cols.values()]
    overall = ('failed' if 'failed' in statuses else 'closed' if state == 'denied'
               else 'done' if state == 'settled' else 'progress')
    return {'id': t['id'], 'qty': t['qty'], 'qty_text': grp(t['qty']), 'offer': rate(t['offer']), 'buyer': t['buyer'],
            'destination': dest, 'state': state, 'created': t['created'], 'overall': overall, 'problem': problem,
            'can_decide': state == 'created' and bool(p) and not step, 'can_retry': bool(broken),
            'profit': p['profit'] if p else None, 'columns': cols}


def board():
    txns = [db.load(r) for r in db.rows('SELECT * FROM transactions ORDER BY seq DESC LIMIT 100')]
    rows = [board_row(t) for t in txns]
    count = lambda s: sum(1 for r in rows if r['overall'] == s)
    settled = [r['profit'] for r in rows if r['state'] == 'settled']
    return {
        'now': config.now(),
        'rows': rows,
        'totals': {'all': len(rows), 'done': count('done'), 'progress': count('progress'), 'failed': count('failed'),
                   'profit': inr(sum(settled)) if settled else inr(0)},
        'warehouse': agents.DataWarehouseAgent.snapshot(),
    }


def app_state():
    txns = [db.load(r) for r in db.rows('SELECT * FROM transactions ORDER BY seq')]
    waiting = [t for t in txns if t['state'] == 'created' and t['plan'] and not t['step']]
    return {
        'card': card_view(waiting[0]) if waiting else None,
        'waiting': len(waiting),
        'active': [summary(t) for t in txns if t['state'] not in FINAL][-5:],
        'notifications': db.rows('SELECT id,txn,kind,title,body,created FROM notifications ORDER BY id DESC LIMIT 40'),  # newest first
        'warehouse': agents.DataWarehouseAgent.snapshot(),
        'push': notify.push_available(),
    }
