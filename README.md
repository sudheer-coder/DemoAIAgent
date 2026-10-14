# AgriPilot — AI Supply Chain Brain

You send one message, tap once, and close the app. The business runs itself — and tells you what happened.

Built to `workflow-spec-draft.md` (v1). Demo numbers are illustrative, not real transactions.

## Run it
    pip install -r requirements.txt
    python server.py
Owner app: http://127.0.0.1:8765  ·  Control room dashboard: http://127.0.0.1:8765/buyer (also /dashboard)  (Windows: START_WINDOWS.bat)

`/buyer` opens as a **chat** (like Claude or ChatGPT): type or speak (🎙️ mic) the buyer's order and a live card with a
4-step tracker appears in the conversation, with Approve / Deny on it. You can also ask "status of T3", "approve T3",
"retry T3", "what needs me?" or "today's summary". The mic uses the browser's voice typing (Chrome/Edge) and needs
https or localhost; on a phone over plain Wi-Fi, use the keyboard's mic button.

The **Dashboard** tab shows every transaction across four columns:
**Transaction → Price approval → Transport → Load delivered**. Each column is green (active / done), orange
(in progress, with a countdown) or red (failed, needs attention). A red row shows the reason and a **Retry** button.
You can also Approve / Deny right on the board. When `OWNER_KEY` is set, the board is visible only on the owner's device.

**On your phone:** `START_WINDOWS.bat` listens on your Wi-Fi and prints an address like `http://192.168.1.5:8765/buyer`.
Open it on a phone connected to the same Wi-Fi (allow Python through the Windows firewall when asked). On a phone the
board shows one card per transaction with a 4-step tracker, and the order box sits at the bottom like a chat.
Push notifications on the phone need HTTPS (e.g. the Render deploy); over plain Wi-Fi the dashboard still updates live.

1. On `/buyer`, send: `Need 10,000 coconuts, 18 rupees per coconut`.
2. About 10 s later the owner app shows the approval card. Tap **Approve** (or **Deny**) — the only decision.
3. Close the app. Push notifications arrive 15 s, 35 s and 50 s after Approve: scheduled, delivered, completed.

Notifications need HTTPS (or localhost). Tap "Turn on notifications" once in the owner app.
`AGRIPILOT_SPEED=10` runs the whole demo 10x faster.

## How it works
    agripilot/agents.py      Middlemen, Smart Price, Data Warehouse, Co-ordination, Live Feed
    agripilot/supervisor.py  the Supervisor agent: the flow, the clock, payment verification, what the app shows
    agripilot/notify.py      the checkpoint notifications + Web Push
    agripilot/db.py          SQLite; one `transactions` row carries the whole deal
    agripilot/web.py         routes: /api/order (buyer), /api/state, /api/board, /api/decision, /api/retry (owner)

States: `created → in progress → goods completed → payment pending → settled` (or `denied`).
The owner never types: the app has no text inputs. Denying sends no notification.

## What is real and what is a stand-in
The workflow, pricing math, stock handling, timings and notifications are real and tested.
These are **simulated** and marked `SEAM` in `agents.py` — nothing leaves the server:
- **Live data feed**: fixed demo values (override with `FEED_FARMER_RATE`, `FEED_TRANSPORT`, `FEED_LABOUR`, `FEED_TOLL`).
- **Co-ordination**: demo farmer/lorry roster. No real messages to farmers, lorry booking, crew or quality-grader routing.
- **Paperwork**: e-way bill and GST numbers are generated placeholders.
- **Payments**: recorded and verified against the deal, but no money moves.
- **Buyer channel**: `/buyer` stands in for WhatsApp/SMS. Anything that can POST `{"text": "..."}` to `/api/order` works.

## Put it online (Render)
1. render.com → **New → Blueprint** → pick this repository → **Apply**. `render.yaml` sets everything up.
2. When it says *Live*, open the service → **Environment** and copy the `OWNER_KEY` value.
3. Your private link is `https://<your-service>.onrender.com/buyer?key=<OWNER_KEY>`. Open it once on each device
   (it is remembered, and the key is removed from the address bar). Without the key anyone can send an order but
   only you see the live cards and can approve.
Free plan: it sleeps after ~15 min idle (first visit then takes ~30-50 s) and data resets when it restarts.

## Settings
`OWNER_KEY` (open `/?key=<OWNER_KEY>` once on the phone to lock the owner app), `AGRIPILOT_DB`, `AGRIPILOT_SPEED`,
`STARTING_STOCK`, `BUYER_DESTINATION`, `HOST`, `PORT`. On Render's free plan the SQLite file is wiped on restart.

## Tests
    python -m unittest discover -s tests
