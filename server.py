import json
import mimetypes
import os
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import api_skills  # noqa: F401 (registers /api/skills routes)
import api_tasks  # noqa: F401 (registers /api/tasks routes)
import api_tokens  # noqa: F401 (registers /api/overview, /api/sessions routes)
import auth
import db
import users  # noqa: F401 (registers /api/users route)
from routes import ROUTES, ApiError, ResponseHelper, route

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "127.0.0.1"
PORT = 8000

CLEAN_URLS = {
    "/": "/index.html",
    "/login": "/login.html",
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
        for m, pattern, fn, public in ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            session = self._get_session()
            if not public and session is None:
                self._send_json(401, {"error": "unauthorized"})
                return
            resp = ResponseHelper()
            try:
                body = self._read_json_body()
                result = fn(match, query, body, session, resp)
                self._send_json(200, result, extra_headers=resp.extra_headers)
            except ApiError as e:
                self._send_json(e.status, {"error": e.message}, extra_headers=resp.extra_headers)
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {"error": str(e)})
            return
        self._send_json(404, {"error": f"no route for {method} {path}"})

    def _get_session(self):
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(auth.SESSION_COOKIE_NAME + "="):
                return auth.get_session(part.split("=", 1)[1])
        return None

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw)

    def _send_json(self, status, obj, extra_headers=None):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in extra_headers or []:
            self.send_header(name, value)
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


@route("GET", r"/api/health", public=True)
def health(match, query, body, session, resp):
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
