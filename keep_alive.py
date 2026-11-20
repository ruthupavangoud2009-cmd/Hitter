"""
Render keep-alive wrapper for bot_v2.py.

This file starts the Discord bot as a child process and exposes a tiny HTTP
health endpoint so an external monitor (such as UptimeRobot) can ping the
Render Web Service.

Keep bot_v2.py and settings.py unchanged.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT_FILE = BASE_DIR / "bot_v2.py"
PORT = int(os.environ.get("PORT", "10000"))

stop_event = threading.Event()
process_lock = threading.Lock()
bot_process = None


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Any path (including /) can be used as the UptimeRobot monitor URL.
        with process_lock:
            running = bot_process is not None and bot_process.poll() is None

        if running:
            self.send_response(200)
            body = b"Discord bot process is running."
        else:
            self.send_response(503)
            body = b"Discord bot process is not running."

        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        # Keep routine health-check requests out of the Render logs.
        return


def supervise_bot():
    """Start the bot and restart it if its process exits unexpectedly."""
    global bot_process

    while not stop_event.is_set():
        if not BOT_FILE.is_file():
            print(f"ERROR: {BOT_FILE.name} was not found in {BASE_DIR}", flush=True)
            stop_event.wait(30)
            continue

        print(f"Starting {BOT_FILE.name}...", flush=True)
        try:
            child = subprocess.Popen(
                [sys.executable, "-u", str(BOT_FILE)],
                cwd=str(BASE_DIR),
                env=os.environ.copy(),
            )
            with process_lock:
                bot_process = child

            return_code = child.wait()
            with process_lock:
                if bot_process is child:
                    bot_process = None

            if not stop_event.is_set():
                print(
                    f"{BOT_FILE.name} exited with code {return_code}; "
                    "restarting in 5 seconds...",
                    flush=True,
                )
                stop_event.wait(5)

        except Exception as exc:
            print(f"Could not start {BOT_FILE.name}: {exc}", flush=True)
            stop_event.wait(5)


def shutdown(signum=None, frame=None):
    """Ask the child bot process to stop when Render shuts down the service."""
    stop_event.set()

    with process_lock:
        child = bot_process

    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

    # The HTTP server is shut down by the main thread after this event.
    print("Shutdown requested.", flush=True)


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.timeout = 1

    bot_thread = threading.Thread(target=supervise_bot, daemon=True)
    bot_thread.start()

    print(f"Health server listening on 0.0.0.0:{PORT}", flush=True)

    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        shutdown()
        bot_thread.join(timeout=2)


if __name__ == "__main__":
    main()
