"""AgriPilot — AI supply chain brain for a coconut trader.

You send one message, tap once, and close the app. The business runs itself, and tells you what happened.
Start:  python server.py   then open http://127.0.0.1:8765 (owner) and /buyer (the control-room dashboard).
Phone:  set HOST=0.0.0.0 (START_WINDOWS.bat does this) and open the "On your phone" address it prints.
"""
import socket
import threading
from http.server import ThreadingHTTPServer

from agripilot import agents, config, db, supervisor, web


def build_server():
    db.init(agents.LiveFeed.snapshot()['farmer_rate'])
    return ThreadingHTTPServer((config.HOST, config.PORT), web.Handler)


def lan_address():
    """This computer's address on the Wi-Fi, so a phone on the same network can reach it. No traffic is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
    except OSError:
        return None


if __name__ == '__main__':
    server = build_server()
    threading.Thread(target=supervisor.worker, daemon=True).start()
    port = server.server_address[1]
    print('AgriPilot is running.', flush=True)
    print('  On this computer:  http://127.0.0.1:%d/buyer   (owner app: http://127.0.0.1:%d)' % (port, port), flush=True)
    ip = lan_address() if config.HOST in ('0.0.0.0', '') else None
    if ip:
        print('  On your phone:     http://%s:%d/buyer   (same Wi-Fi as this computer)' % (ip, port), flush=True)
    server.serve_forever()
