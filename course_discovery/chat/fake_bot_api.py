from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    sent: list[dict] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path.endswith("/sendMessage"):
                with lock:
                    sent.append(json.loads(body))
                return self._json({"ok": True, "result": {"message_id": len(sent)}})
            if self.path == "/reset":
                with lock:
                    sent.clear()
                return self._json({"ok": True})
            self._json({"ok": False}, 404)

        def do_GET(self) -> None:
            if self.path == "/sent":
                with lock:
                    return self._json(list(sent))
            self._json({"ok": False}, 404)

        def _json(self, payload, status: int = 200) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    server = make_server(parser.parse_args().port)
    print(server.server_address[1], flush=True)
    server.serve_forever()
