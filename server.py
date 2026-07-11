import json
import mimetypes
import os
import subprocess
import threading
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import api_feedback  # noqa: F401 (registers /api/feedback routes)
import api_skills  # noqa: F401 (registers /api/skills routes)
import api_tasks  # noqa: F401 (registers /api/tasks routes)
import api_tokens  # noqa: F401 (registers /api/overview, /api/sessions routes)
import auth
import db
import users  # noqa: F401 (registers /api/users route)
from routes import ROUTES, ApiError, ResponseHelper, route

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def _detect_vpn_ip():
    """This machine's Tailscale IP (100.64.0.0/10 CGNAT range), if connected.

    We bind to this specific address rather than 0.0.0.0 so the admin panel
    (which can toggle skills, control launchd tasks, and drives an
    autonomous code-editing feedback loop) is reachable from the VPN without
    also being exposed on every other network this laptop happens to join
    (home wifi, public wifi, ...). Falls back to loopback-only if the VPN
    isn't up.
    """
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("inet "):
            continue
        ip = line.split()[1]
        octets = ip.split(".")
        if len(octets) != 4:
            continue
        try:
            octets = [int(o) for o in octets]
        except ValueError:
            continue
        if octets[0] == 100 and 64 <= octets[1] <= 127:
            return ip
    return None


def _bind_hosts():
    """Always loopback (so the person sitting at this Mac keeps working), plus the
    VPN IP if connected (so VPN peers can reach it too) — never 0.0.0.0."""
    hosts = ["127.0.0.1"]
    vpn_ip = _detect_vpn_ip()
    if vpn_ip:
        hosts.append(vpn_ip)
    return hosts


PORT = 8000

CLEAN_URLS = {
    "/": "/index.html",
    "/login": "/login.html",
    "/skills": "/skills.html",
    "/tasks": "/tasks.html",
    "/feedback": "/feedback.html",
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

    servers = []
    for host in _bind_hosts():
        server = ThreadingHTTPServer((host, PORT), Handler)
        servers.append(server)
        print(f"admin-web listening on http://{host}:{PORT}")

    background = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers[1:]]
    for t in background:
        t.start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
