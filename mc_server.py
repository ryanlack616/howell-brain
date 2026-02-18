"""
Mission Control Server — serves static files + inbox API
Run: python mc_server.py
Browse: http://localhost:8111

POST /api/inbox  — persist full inbox array to disk
GET  /api/inbox  — read current inbox from disk
"""

import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8111
DIR = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(DIR, "inbox.json")


class MCHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/inbox":
            self._send_json(self._load_inbox())
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/inbox":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body) if length else []

                # Accept either a bare array or {messages: [...]}
                if isinstance(data, list):
                    messages = data
                elif isinstance(data, dict) and "messages" in data:
                    messages = data["messages"]
                else:
                    messages = []

                payload = {
                    "updated": datetime.utcnow().isoformat() + "Z",
                    "messages": messages,
                }
                self._save_inbox(payload)
                self._send_json({"ok": True, "count": len(messages)})
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
            except Exception as e:
                self._send_error(500, str(e))
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data):
        out = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(out)

    def _send_error(self, code, msg):
        self._send_json({"error": msg, "code": code})

    def _load_inbox(self):
        if os.path.exists(INBOX):
            with open(INBOX, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"messages": [], "updated": None}

    def _save_inbox(self, data):
        with open(INBOX, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def log_message(self, format, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        req = args[0] if args else ""
        if "/api/" in req:
            sys.stderr.write(f"  [{ts}] {req}\n")


if __name__ == "__main__":
    os.chdir(DIR)
    server = HTTPServer(("", PORT), MCHandler)
    print(f"""
  Mission Control — Claude-Howell
  http://localhost:{PORT}
  Inbox: {INBOX}
  Serving: {DIR}
  Ctrl+C to stop
    """.strip())
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
