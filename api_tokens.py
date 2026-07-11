import json
import os
from datetime import datetime, timedelta

import db
from routes import ApiError, route

PRICING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json")

RANGE_DAYS = {"7d": 7, "30d": 30, "1y": 365}


def _load_pricing():
    with open(PRICING_PATH, encoding="utf-8") as f:
        return json.load(f)


def _price_for_model(pricing, model):
    if not model:
        return pricing["_default"]
    models = pricing["models"]
    if model in models:
        return models[model]
    for key in models:
        if model.startswith(key):
            return models[key]
    return pricing["_default"]


def _estimate_cost(pricing, model, input_tok, output_tok, cache_read_tok, cache_creation_tok):
    if model == "<synthetic>":
        return 0.0
    p = _price_for_model(pricing, model)
    return (
        (input_tok or 0) / 1_000_000 * p["input"]
        + (output_tok or 0) / 1_000_000 * p["output"]
        + (cache_read_tok or 0) / 1_000_000 * p["cache_read"]
        + (cache_creation_tok or 0) / 1_000_000 * p["cache_write_5m"]
    )


def _range_cutoff(range_key):
    if range_key == "all":
        return None
    days = RANGE_DAYS.get(range_key, 7)
    cutoff = datetime.utcnow() - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _single(query, name, default=None):
    values = query.get(name)
    return values[0] if values else default


def _where(range_key, user, project=None, extra_prefix="u"):
    conditions = []
    params = []
    cutoff = _range_cutoff(range_key)
    if cutoff:
        conditions.append(f"{extra_prefix}.ts >= ?")
        params.append(cutoff)
    if user:
        conditions.append(f"{extra_prefix}.owner_user = ?")
        params.append(user)
    if project:
        conditions.append(f"{extra_prefix}.project = ?")
        params.append(project)
    sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return sql, params


def _row_totals(rows, pricing, model_key="model"):
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0}
    for r in rows:
        i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
        totals["input"] += i
        totals["output"] += o
        totals["cache_read"] += cr
        totals["cache_creation"] += cc
        totals["cost_usd"] += _estimate_cost(pricing, r[model_key] if model_key in r.keys() else None, i, o, cr, cc)
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return totals


@route("GET", r"/api/overview")
def overview(match, query, body, session, resp):
    range_key = _single(query, "range", "7d")
    user = _single(query, "user")
    pricing = _load_pricing()
    where_sql, params = _where(range_key, user, extra_prefix="usage_events")

    conn = db.get_conn()
    cur = conn.cursor()

    daily_rows = cur.execute(
        f"""SELECT date(ts) as day, model,
                   SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc
            FROM usage_events {where_sql}
            GROUP BY day, model ORDER BY day""",
        params,
    ).fetchall()
    daily = {}
    for r in daily_rows:
        day = r["day"]
        entry = daily.setdefault(day, {"day": day, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0})
        i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
        entry["input"] += i
        entry["output"] += o
        entry["cache_read"] += cr
        entry["cache_creation"] += cc
        entry["cost_usd"] += _estimate_cost(pricing, r["model"], i, o, cr, cc)
    daily_list = sorted(daily.values(), key=lambda e: e["day"])
    for e in daily_list:
        e["cost_usd"] = round(e["cost_usd"], 4)

    by_model_rows = cur.execute(
        f"""SELECT model, SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc, COUNT(*) n
            FROM usage_events {where_sql} GROUP BY model""",
        params,
    ).fetchall()
    by_model = []
    for r in by_model_rows:
        cost = _estimate_cost(pricing, r["model"], r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0)
        by_model.append({
            "model": r["model"], "input": r["i"] or 0, "output": r["o"] or 0,
            "cache_read": r["cr"] or 0, "cache_creation": r["cc"] or 0,
            "cost_usd": round(cost, 4), "count": r["n"],
        })
    by_model.sort(key=lambda e: e["cost_usd"], reverse=True)

    by_project_rows = cur.execute(
        f"""SELECT project, model, SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc
            FROM usage_events {where_sql} GROUP BY project, model""",
        params,
    ).fetchall()
    by_project = {}
    for r in by_project_rows:
        key = r["project"] or "(unknown)"
        entry = by_project.setdefault(key, {"project": key, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0})
        i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
        entry["input"] += i
        entry["output"] += o
        entry["cache_read"] += cr
        entry["cache_creation"] += cc
        entry["cost_usd"] += _estimate_cost(pricing, r["model"], i, o, cr, cc)
    by_project_list = sorted(by_project.values(), key=lambda e: e["cost_usd"], reverse=True)
    for e in by_project_list:
        e["cost_usd"] = round(e["cost_usd"], 4)

    by_user_rows = cur.execute(
        f"""SELECT owner_user, model, SUM(input_tokens) i, SUM(output_tokens) o,
                   SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc
            FROM usage_events {where_sql} GROUP BY owner_user, model""",
        params,
    ).fetchall()
    by_user = {}
    for r in by_user_rows:
        key = r["owner_user"] or "(unknown)"
        entry = by_user.setdefault(key, {"owner_user": key, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0})
        i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
        entry["input"] += i
        entry["output"] += o
        entry["cache_read"] += cr
        entry["cache_creation"] += cc
        entry["cost_usd"] += _estimate_cost(pricing, r["model"], i, o, cr, cc)
    by_user_list = sorted(by_user.values(), key=lambda e: e["cost_usd"], reverse=True)
    for e in by_user_list:
        e["cost_usd"] = round(e["cost_usd"], 4)

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0}
    for m in by_model:
        totals["input"] += m["input"]
        totals["output"] += m["output"]
        totals["cache_read"] += m["cache_read"]
        totals["cache_creation"] += m["cache_creation"]
        totals["cost_usd"] += m["cost_usd"]
    totals["cost_usd"] = round(totals["cost_usd"], 4)

    conn.close()
    return {
        "range": range_key,
        "totals": totals,
        "daily": daily_list,
        "by_model": by_model,
        "by_project": by_project_list,
        "by_user": by_user_list,
    }


@route("GET", r"/api/sessions")
def list_sessions(match, query, body, session, resp):
    range_key = _single(query, "range", "7d")
    user = _single(query, "user")
    project = _single(query, "project")
    sort = _single(query, "sort", "time")
    limit = int(_single(query, "limit", "200"))
    pricing = _load_pricing()
    where_sql, params = _where(range_key, user, project, extra_prefix="u")

    conn = db.get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        f"""SELECT s.session_id, s.owner_user, s.title, s.first_user_message,
                   s.project, s.first_ts, s.last_ts, u.model,
                   SUM(u.input_tokens) i, SUM(u.output_tokens) o,
                   SUM(u.cache_read_tokens) cr, SUM(u.cache_creation_tokens) cc
            FROM sessions s JOIN usage_events u ON u.session_id = s.session_id
            {where_sql}
            GROUP BY s.session_id, u.model""",
        params,
    ).fetchall()
    conn.close()

    by_session = {}
    for r in rows:
        sid = r["session_id"]
        entry = by_session.setdefault(sid, {
            "session_id": sid, "owner_user": r["owner_user"],
            "title": r["title"] or r["first_user_message"] or "(untitled)",
            "project": r["project"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
            "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0,
        })
        i, o, cr, cc = r["i"] or 0, r["o"] or 0, r["cr"] or 0, r["cc"] or 0
        entry["input"] += i
        entry["output"] += o
        entry["cache_read"] += cr
        entry["cache_creation"] += cc
        entry["cost_usd"] += _estimate_cost(pricing, r["model"], i, o, cr, cc)

    result = list(by_session.values())
    for e in result:
        e["cost_usd"] = round(e["cost_usd"], 4)
    if sort == "cost":
        result.sort(key=lambda e: e["cost_usd"], reverse=True)
    else:
        result.sort(key=lambda e: e["last_ts"] or "", reverse=True)
    return {"sessions": result[:limit]}


@route("GET", r"/api/sessions/(?P<session_id>[\w-]+)")
def session_detail(match, query, body, session, resp):
    session_id = match.group("session_id")
    pricing = _load_pricing()

    conn = db.get_conn()
    cur = conn.cursor()
    meta = cur.execute(
        "SELECT session_id, owner_user, title, first_user_message, project, first_ts, last_ts FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not meta:
        conn.close()
        raise ApiError(404, "session not found")

    rows = cur.execute(
        """SELECT uuid, parent_uuid, ts, model, input_tokens, output_tokens,
                  cache_read_tokens, cache_creation_tokens, is_sidechain, tools, text_preview
           FROM usage_events WHERE session_id = ? ORDER BY ts""",
        (session_id,),
    ).fetchall()
    conn.close()

    def to_entry(r):
        cost = _estimate_cost(pricing, r["model"], r["input_tokens"], r["output_tokens"],
                               r["cache_read_tokens"], r["cache_creation_tokens"])
        return {
            "uuid": r["uuid"], "parent_uuid": r["parent_uuid"], "ts": r["ts"], "model": r["model"],
            "input": r["input_tokens"], "output": r["output_tokens"],
            "cache_read": r["cache_read_tokens"], "cache_creation": r["cache_creation_tokens"],
            "cost_usd": round(cost, 4),
            "tools": [t for t in (r["tools"] or "").split(",") if t],
            "text_preview": r["text_preview"],
        }

    main_thread = [to_entry(r) for r in rows if not r["is_sidechain"]]
    sidechain = [to_entry(r) for r in rows if r["is_sidechain"]]

    return {
        "session_id": meta["session_id"],
        "owner_user": meta["owner_user"],
        "title": meta["title"] or meta["first_user_message"] or "(untitled)",
        "project": meta["project"],
        "first_ts": meta["first_ts"],
        "last_ts": meta["last_ts"],
        "main_thread": main_thread,
        "sidechain": sidechain,
    }
