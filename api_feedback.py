from datetime import datetime, timezone

import db
from routes import ApiError, route


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@route("POST", r"/api/feedback")
def create_ticket(match, query, body, session, resp):
    title = (body or {}).get("title", "").strip()
    description = (body or {}).get("description", "").strip()
    page = (body or {}).get("page", "")
    if not title or not description:
        raise ApiError(400, "title and description required")

    now = _now()
    content = description + (f"\n\n(页面: {page})" if page else "")

    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO feedback_tickets(owner_user, title, status, created_at, updated_at)
           VALUES (?, ?, 'open', ?, ?)""",
        (session["username"], title, now, now),
    )
    ticket_id = cur.lastrowid
    cur.execute(
        "INSERT INTO feedback_messages(ticket_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (ticket_id, content, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": ticket_id}


@route("GET", r"/api/feedback")
def list_tickets(match, query, body, session, resp):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, owner_user, title, status, created_at, updated_at FROM feedback_tickets ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return {"tickets": [dict(r) for r in rows]}


@route("GET", r"/api/feedback/(?P<ticket_id>\d+)")
def ticket_detail(match, query, body, session, resp):
    ticket_id = int(match.group("ticket_id"))
    conn = db.get_conn()
    ticket = conn.execute(
        "SELECT id, owner_user, title, status, created_at, updated_at, claude_session_id FROM feedback_tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if not ticket:
        conn.close()
        raise ApiError(404, "ticket not found")
    messages = conn.execute(
        "SELECT id, role, content, created_at FROM feedback_messages WHERE ticket_id = ? ORDER BY created_at",
        (ticket_id,),
    ).fetchall()
    conn.close()
    return {"ticket": dict(ticket), "messages": [dict(m) for m in messages]}


@route("POST", r"/api/feedback/(?P<ticket_id>\d+)/reply")
def reply_ticket(match, query, body, session, resp):
    ticket_id = int(match.group("ticket_id"))
    content = (body or {}).get("content", "").strip()
    if not content:
        raise ApiError(400, "content required")

    conn = db.get_conn()
    ticket = conn.execute("SELECT id FROM feedback_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        raise ApiError(404, "ticket not found")

    now = _now()
    conn.execute(
        "INSERT INTO feedback_messages(ticket_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (ticket_id, content, now),
    )
    conn.execute(
        "UPDATE feedback_tickets SET status = 'open', updated_at = ? WHERE id = ?",
        (now, ticket_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}
