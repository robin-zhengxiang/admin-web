import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeMatch:
    """Minimal stand-in for re.Match — route handlers only ever call .group(name)."""

    def __init__(self, groups):
        self._groups = groups

    def group(self, name):
        return self._groups[name]


def fake_session(username, uid=501):
    return {"username": username, "uid": uid, "expires": float("inf")}


def make_fake_user(base_dir, username, uid=501, gid=20):
    """Build a throwaway ~<username> layout under base_dir. Never touches the real /Users/*."""
    home = os.path.join(base_dir, username)
    os.makedirs(os.path.join(home, ".claude", "skills"), exist_ok=True)
    os.makedirs(os.path.join(home, ".claude", "projects"), exist_ok=True)
    os.makedirs(os.path.join(home, "Library", "LaunchAgents"), exist_ok=True)
    return {"username": username, "uid": uid, "gid": gid, "home": home}
