import secrets
import subprocess
import time

import users
from routes import ApiError, route

SESSION_COOKIE_NAME = "admin_web_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600

_sessions = {}  # session_id -> {"username": str, "uid": int, "expires": float}


def verify_password(username, password):
    try:
        proc = subprocess.run(
            ["dscl", ".", "-authonly", username],
            input=password, text=True,
            capture_output=True, timeout=10,
        )
    except Exception:
        return False
    return proc.returncode == 0


def create_session(username, uid):
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "username": username,
        "uid": uid,
        "expires": time.time() + SESSION_TTL_SECONDS,
    }
    return session_id


def get_session(session_id):
    if not session_id:
        return None
    entry = _sessions.get(session_id)
    if not entry:
        return None
    if entry["expires"] < time.time():
        del _sessions[session_id]
        return None
    return entry


def delete_session(session_id):
    _sessions.pop(session_id, None)


@route("POST", r"/api/login", public=True)
def login(match, query, body, session, resp):
    username = (body or {}).get("username", "").strip()
    password = (body or {}).get("password", "")
    if not username or not password:
        raise ApiError(400, "username and password required")
    user = users.get_user(username)
    if not user:
        raise ApiError(401, "unknown local user")
    if not verify_password(username, password):
        raise ApiError(401, "invalid credentials")
    session_id = create_session(username, user["uid"])
    resp.set_cookie(SESSION_COOKIE_NAME, session_id, max_age=SESSION_TTL_SECONDS)
    return {"ok": True, "username": username}


@route("POST", r"/api/logout")
def logout(match, query, body, session, resp):
    for sid, entry in list(_sessions.items()):
        if entry is session:
            delete_session(sid)
            break
    resp.clear_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@route("GET", r"/api/me")
def me(match, query, body, session, resp):
    return {"username": session["username"], "uid": session["uid"]}
