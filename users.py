import os
import pwd

MIN_UID = 500  # macOS interactive accounts are normally >= 501; below that is system/service accounts
EXCLUDED_USERNAMES = {"guest", "nobody", "daemon", "root"}


def list_local_users():
    users = []
    seen_home = set()
    for entry in pwd.getpwall():
        if entry.pw_uid < MIN_UID:
            continue
        if entry.pw_name.lower() in EXCLUDED_USERNAMES:
            continue
        home = entry.pw_dir
        if not home or not home.startswith("/Users/") or not os.path.isdir(home):
            continue
        if home in seen_home:
            continue
        seen_home.add(home)
        users.append({
            "username": entry.pw_name,
            "uid": entry.pw_uid,
            "gid": entry.pw_gid,
            "home": home,
        })
    return users


def get_user(username):
    for u in list_local_users():
        if u["username"] == username:
            return u
    return None


def get_user_by_uid(uid):
    for u in list_local_users():
        if u["uid"] == uid:
            return u
    return None
