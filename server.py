import json
import mimetypes
import os
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import db
from routes import ROUTES, ApiError, route

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "127.0.0.1"
PORT = 8787

CLEAN_URLS = {
    "/": "/index.html",
    "/skills": "/skills.html",
    "/tasks": "/tasks.html",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._dispatch_api(method, path, query)
        elif method == "GET":
            self._serve_static(path)
        else:
            self._send_json(405, {"error": "method not allowed"})

    def _dispatch_api(self, method, path, query):
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            try:
                body = self._read_json_body()
                result = fn(match, query, body)
                self._send_json(200, result)
            except ApiError as e:
                self._send_json(e.status, {"error": e.message})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {"error": str(e)})
            return
        self._send_json(404, {"error": f"no route for {method} {path}"})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw)

    def _send_json(self, status, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path):
        path = CLEAN_URLS.get(path, path)
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not (full == STATIC_DIR or full.startswith(STATIC_DIR + os.sep)):
            self._send_json(403, {"error": "forbidden"})
            return
        if not os.path.isfile(full):
            self._send_json(404, {"error": "not found"})
            return
        content_type, _ = mimetypes.guess_type(full)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@route("GET", r"/api/health")
def health(match, query, body):
    return {"status": "ok"}


def main():
    db.init_db()
    db.start_indexer_thread()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"admin-web listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
