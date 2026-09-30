"""A stand-in for SteelSeries GG's GameSense server, for testing the .exe in CI.

    python ci_fake_gg.py PORT LOG

Accepts GameSense requests like GG, and appends one line per request to LOG:
the endpoint, the event name, and the size of any bitmap it carried.
`python ci_fake_gg.py --check LOG` then verifies that bitmap frames arrived.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve(port, log):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"             # keep-alive, like the real server

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            frame = (body.get("data") or {}).get("frame") or {}
            sizes = {k: len(v) for k, v in frame.items() if k.startswith("image-data-")}
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"endpoint": self.path.lstrip("/"), "event": body.get("event"),
                                    "bitmaps": sizes}) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def check(log):
    with open(log, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    frames = [e for e in entries if e["endpoint"] == "game_event" and e["event"] == "SCREEN"]
    good = [e for e in frames if e["bitmaps"] == {"image-data-128x40": 640}]
    registered = any(e["endpoint"] == "bind_game_event" and e["event"] == "SCREEN" for e in entries)
    released = any(e["endpoint"] == "stop_game" for e in entries)
    print(f"Fake GG received {len(entries)} requests: {len(frames)} screen frames "
          f"({len(good)} with a 640-byte 128x40 bitmap), registered: {registered}, released: {released}")
    return registered and released and len(good) >= 5 and len(good) == len(frames)


if __name__ == "__main__":
    if sys.argv[1] == "--check":
        sys.exit(0 if check(sys.argv[2]) else 1)
    serve(int(sys.argv[1]), sys.argv[2])
