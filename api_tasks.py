import glob
import os
import plistlib
import subprocess
import sys

import crontab
import users
from routes import ApiError, route

TASKCTL_DIR = os.path.expanduser("~/.claude/skills/task-manager")
sys.path.insert(0, TASKCTL_DIR)
import taskctl  # noqa: E402

LAUNCH_AGENTS_SUBDIR = "Library/LaunchAgents"


def _query_single(query, name, default=None):
    values = query.get(name)
    return values[0] if values else default


def _current_uid():
    return os.getuid()


def _launchctl_for_user(uid, args):
    if uid == _current_uid():
        cmd = ["launchctl"] + args
    else:
        cmd = ["launchctl", "asuser", str(uid), "launchctl"] + args
    return subprocess.run(cmd, capture_output=True, text=True)


def _launchctl_rows_for(uid):
    r = _launchctl_for_user(uid, ["list"])
    rows = {}
    for line in r.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            rows[parts[2]] = (parts[0], parts[1])
    return rows


def _status_of(label, rows):
    if label not in rows:
        return "disabled", None
    pid, _ = rows[label]
    if pid.isdigit():
        return "running", pid
    return "enabled", None


def _registered_tasks_for(username):
    """task-manager/tasks.json entries — currently only meaningful for the user who owns that registry."""
    if username != "robinzheng":
        return {}
    try:
        return {t["label"]: t for t in taskctl.load_tasks()}
    except Exception:
        return {}


def _plist_label_and_data(path):
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return data.get("Label"), data
    except Exception:
        return None, None


def _require_owner(session, owner_user):
    if session["username"] != owner_user:
        raise ApiError(403, f"only {owner_user} can modify this")


def _find_task(owner_user, label):
    user = users.get_user(owner_user)
    if not user:
        raise ApiError(404, "unknown user")
    path = os.path.join(user["home"], LAUNCH_AGENTS_SUBDIR, label + ".plist")
    if not os.path.isfile(path):
        raise ApiError(404, "task not found")
    return user, path


@route("GET", r"/api/tasks")
def list_tasks(match, query, body, session, resp):
    result = []
    for user in users.list_local_users():
        registered = _registered_tasks_for(user["username"])
        rows = _launchctl_rows_for(user["uid"])
        pattern = os.path.join(user["home"], LAUNCH_AGENTS_SUBDIR, "*.plist")
        try:
            paths = glob.glob(pattern)
        except PermissionError:
            paths = []
        for path in paths:
            label, plist_data = _plist_label_and_data(path)
            if not label:
                continue
            reg = registered.get(label)
            status, pid = _status_of(label, rows)
            schedule = (plist_data or {}).get("StartCalendarInterval")
            result.append({
                "owner_user": user["username"],
                "label": label,
                "name": reg["name"] if reg else label,
                "desc": reg["desc"] if reg else "",
                "type": reg["type"] if reg else ("scheduled" if schedule else "daemon"),
                "schedule": schedule,
                "cron": crontab.launchd_to_cron(schedule) if schedule else None,
                "log": reg["log"] if reg else None,
                "status": status,
                "pid": pid,
            })
    result.sort(key=lambda t: (t["owner_user"], t["name"]))
    return {"tasks": result}


@route("POST", r"/api/tasks/(?P<owner_user>[^/]+)/(?P<label>[^/]+)/enable")
def enable_task(match, query, body, session, resp):
    owner_user, label = match.group("owner_user"), match.group("label")
    _require_owner(session, owner_user)
    user, path = _find_task(owner_user, label)
    r = _launchctl_for_user(user["uid"], ["load", "-w", path])
    return {"ok": r.returncode == 0, "detail": (r.stderr or r.stdout).strip()}


@route("POST", r"/api/tasks/(?P<owner_user>[^/]+)/(?P<label>[^/]+)/disable")
def disable_task(match, query, body, session, resp):
    owner_user, label = match.group("owner_user"), match.group("label")
    _require_owner(session, owner_user)
    user, path = _find_task(owner_user, label)
    r = _launchctl_for_user(user["uid"], ["unload", "-w", path])
    return {"ok": r.returncode == 0, "detail": (r.stderr or r.stdout).strip()}


@route("POST", r"/api/tasks/(?P<owner_user>[^/]+)/(?P<label>[^/]+)/run")
def run_task(match, query, body, session, resp):
    owner_user, label = match.group("owner_user"), match.group("label")
    _require_owner(session, owner_user)
    user, _ = _find_task(owner_user, label)
    r = _launchctl_for_user(user["uid"], ["start", label])
    return {"ok": r.returncode == 0, "detail": (r.stderr or r.stdout).strip()}


@route("GET", r"/api/tasks/(?P<owner_user>[^/]+)/(?P<label>[^/]+)/logs")
def task_logs(match, query, body, session, resp):
    owner_user, label = match.group("owner_user"), match.group("label")
    n = int(_query_single(query, "n", "50"))
    _find_task(owner_user, label)  # 404s if the task doesn't exist
    reg = _registered_tasks_for(owner_user).get(label)
    log_path = os.path.expanduser(reg["log"]) if reg and reg.get("log") else None
    if not log_path or not os.path.isfile(log_path):
        return {"lines": [], "note": "no readable log file registered for this task"}
    out = subprocess.run(["tail", "-n", str(n), log_path], capture_output=True, text=True).stdout
    return {"lines": out.splitlines()}


@route("PUT", r"/api/tasks/(?P<owner_user>[^/]+)/(?P<label>[^/]+)/schedule")
def set_schedule(match, query, body, session, resp):
    owner_user, label = match.group("owner_user"), match.group("label")
    _require_owner(session, owner_user)
    user, path = _find_task(owner_user, label)

    cron_expr = ((body or {}).get("cron") or "").strip()
    if not cron_expr:
        raise ApiError(400, "cron required (5 fields: minute hour dom month dow)")
    try:
        intervals = crontab.cron_to_launchd(cron_expr)
    except crontab.CrontabError as e:
        raise ApiError(400, str(e))

    with open(path, "rb") as f:
        plist_data = plistlib.load(f)
    if "StartCalendarInterval" not in plist_data:
        raise ApiError(400, "this task has no schedule (not a scheduled-type task)")

    plist_data["StartCalendarInterval"] = intervals[0] if len(intervals) == 1 else intervals

    try:
        with open(path, "wb") as f:
            plistlib.dump(plist_data, f)
    except PermissionError:
        raise ApiError(403, "no write permission for this user's plist yet (needs the root LaunchDaemon switch)")

    _launchctl_for_user(user["uid"], ["unload", "-w", path])
    _launchctl_for_user(user["uid"], ["load", "-w", path])
    return {"ok": True, "cron": cron_expr, "schedule": plist_data["StartCalendarInterval"]}
