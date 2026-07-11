import glob
import json
import os
import re

import users
from routes import ApiError, route

NAME_RE = re.compile(r"^[\w.-]+$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
VALID_STATES = {"default", "off", "user-invocable-only", "name-only"}


def _skills_dir(home):
    return os.path.join(home, ".claude", "skills")


def _settings_path(home):
    return os.path.join(home, ".claude", "settings.json")


def _parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    fields = {}
    if not m:
        return fields
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def _read_settings(home):
    path = _settings_path(home)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings_atomic(home, uid, gid, settings):
    path = _settings_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    try:
        os.chown(path, uid, gid)
    except (PermissionError, OSError):
        pass  # not running as root yet; ownership fix happens once the LaunchDaemon switch lands


def _require_owner(session, owner_user):
    if session["username"] != owner_user:
        raise ApiError(403, f"only {owner_user} can modify this")


def _skill_dir_for(owner_user, name):
    if not NAME_RE.match(name):
        raise ApiError(400, "invalid skill name")
    user = users.get_user(owner_user)
    if not user:
        raise ApiError(404, "unknown user")
    skill_dir = os.path.join(_skills_dir(user["home"]), name)
    if not os.path.isdir(skill_dir):
        raise ApiError(404, "skill not found")
    return user, skill_dir


@route("GET", r"/api/skills")
def list_skills(match, query, body, session, resp):
    result = []
    for user in users.list_local_users():
        try:
            settings = _read_settings(user["home"])
        except PermissionError:
            continue
        overrides = settings.get("skillOverrides", {})
        pattern = os.path.join(_skills_dir(user["home"]), "*", "SKILL.md")
        try:
            paths = glob.glob(pattern)
        except PermissionError:
            continue
        for path in paths:
            name = os.path.basename(os.path.dirname(path))
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (PermissionError, OSError):
                continue
            fields = _parse_frontmatter(text)
            result.append({
                "owner_user": user["username"],
                "name": fields.get("name", name),
                "description": fields.get("description", ""),
                "state": overrides.get(name, "default"),
            })
    result.sort(key=lambda s: (s["owner_user"], s["name"]))
    return {"skills": result}


@route("POST", r"/api/skills/(?P<owner_user>[^/]+)/(?P<name>[^/]+)/state")
def set_skill_state(match, query, body, session, resp):
    owner_user = match.group("owner_user")
    name = match.group("name")
    state = (body or {}).get("state")
    if state not in VALID_STATES:
        raise ApiError(400, f"state must be one of {sorted(VALID_STATES)}")
    _require_owner(session, owner_user)
    user, _ = _skill_dir_for(owner_user, name)

    settings = _read_settings(user["home"])
    overrides = settings.setdefault("skillOverrides", {})
    if state == "default":
        overrides.pop(name, None)
    else:
        overrides[name] = state
    _write_settings_atomic(user["home"], user["uid"], user["gid"], settings)
    return {"ok": True, "name": name, "state": state}


@route("GET", r"/api/skills/(?P<owner_user>[^/]+)/(?P<name>[^/]+)/content")
def get_skill_content(match, query, body, session, resp):
    owner_user = match.group("owner_user")
    name = match.group("name")
    _, skill_dir = _skill_dir_for(owner_user, name)
    with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
        return {"content": f.read()}


@route("PUT", r"/api/skills/(?P<owner_user>[^/]+)/(?P<name>[^/]+)/content")
def put_skill_content(match, query, body, session, resp):
    owner_user = match.group("owner_user")
    name = match.group("name")
    content = (body or {}).get("content")
    if content is None:
        raise ApiError(400, "content required")
    _require_owner(session, owner_user)
    user, skill_dir = _skill_dir_for(owner_user, name)

    path = os.path.join(skill_dir, "SKILL.md")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)
    try:
        os.chown(path, user["uid"], user["gid"])
    except (PermissionError, OSError):
        pass
    return {"ok": True}
