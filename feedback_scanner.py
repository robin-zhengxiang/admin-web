import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import db

DINGTALK_DIR = os.path.expanduser("~robinzheng/.claude/skills/dingtalk-notify")  # named-user form: correct even if invoked as root
sys.path.insert(0, DINGTALK_DIR)
from headless import CLAUDE_BIN, ALLOWED_TOOLS, DISALLOWED_TOOLS  # noqa: E402 (reuse the reviewed tool permission lists)

ADMIN_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_EXTRA_PATHS = [
    os.path.expanduser("~robinzheng/.local/bin"),
    "/opt/homebrew/bin", "/usr/local/bin",
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
]

PROMPT_TEMPLATE = """这是 admin-web 项目（当前工作目录）收到的一条用户反馈。

标题：{title}

对话记录：
{history}

如果你能确认问题所在并修复，请直接编辑代码修复，修复后用一段话说明改了什么、为什么这样改。
如果信息不足，或者这涉及产品设计/需求取舍而不是单纯的 bug，不要改代码，在回复里提出你需要用户说明的具体问题。"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _build_env():
    """走 Claude Code 订阅 OAuth，不注入 ANTHROPIC_API_KEY（否则会优先于订阅认证）。"""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env["PATH"] = ":".join(_EXTRA_PATHS) + ":" + env.get("PATH", "")
    return env


def _run_claude(prompt, resume_session_id=None, timeout=600):
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ",".join(ALLOWED_TOOLS),
        "--disallowedTools", ",".join(DISALLOWED_TOOLS),
    ]
    if resume_session_id:
        cmd += ["--resume", resume_session_id]
    try:
        proc = subprocess.run(
            cmd, cwd=ADMIN_WEB_DIR, env=_build_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, resume_session_id, f"执行超时（>{timeout}s）"
    try:
        d = json.loads(proc.stdout)
    except Exception:
        return None, resume_session_id, f"输出解析失败：{(proc.stdout or '')[:300]}"
    session_id = d.get("session_id") or resume_session_id
    if d.get("is_error"):
        return None, session_id, d.get("result", "未知错误")
    return d.get("result", ""), session_id, None


def _build_prompt(ticket, messages):
    history = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
    return PROMPT_TEMPLATE.format(title=ticket["title"], history=history)


def _repo_is_clean():
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ADMIN_WEB_DIR, capture_output=True, text=True
    ).stdout
    return not status.strip()


def _current_head():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ADMIN_WEB_DIR, capture_output=True, text=True
    ).stdout.strip()


def _commit_if_changed(before_head):
    """Attribute changes to this ticket's turn only. Caller must guarantee the
    working tree was clean (== before_head) before this ticket's Claude call."""
    if not _repo_is_clean():
        subprocess.run(["git", "add", "-A"], cwd=ADMIN_WEB_DIR)
        subprocess.run(
            ["git", "commit", "-m", "feedback_scanner: auto-fix from user feedback"],
            cwd=ADMIN_WEB_DIR, capture_output=True, text=True,
        )
    after_head = _current_head()
    if after_head == before_head:
        return False, ""
    diff_stat = subprocess.run(
        ["git", "diff", "--stat", before_head, after_head], cwd=ADMIN_WEB_DIR, capture_output=True, text=True
    ).stdout
    return True, diff_stat


def process_ticket(conn, ticket):
    messages = [dict(m) for m in conn.execute(
        "SELECT role, content FROM feedback_messages WHERE ticket_id = ? ORDER BY created_at",
        (ticket["id"],),
    ).fetchall()]
    prompt = _build_prompt(ticket, messages)
    before_head = _current_head()
    result, session_id, error = _run_claude(prompt, resume_session_id=ticket["claude_session_id"])
    now = _now()

    if error:
        conn.execute(
            "UPDATE feedback_tickets SET status='needs_input', claude_session_id=?, updated_at=? WHERE id=?",
            (session_id, now, ticket["id"]),
        )
        conn.execute(
            "INSERT INTO feedback_messages(ticket_id, role, content, created_at) VALUES (?, 'agent', ?, ?)",
            (ticket["id"], f"扫描出错：{error}", now),
        )
        conn.commit()
        return

    changed, diff_stat = _commit_if_changed(before_head)
    status = "resolved" if changed else "needs_input"
    reply = result + (f"\n\n---\n改动摘要：\n{diff_stat}" if changed else "")

    conn.execute(
        "UPDATE feedback_tickets SET status=?, claude_session_id=?, updated_at=? WHERE id=?",
        (status, session_id, now, ticket["id"]),
    )
    conn.execute(
        "INSERT INTO feedback_messages(ticket_id, role, content, created_at) VALUES (?, 'agent', ?, ?)",
        (ticket["id"], reply, now),
    )
    conn.commit()


def main():
    if not _repo_is_clean():
        print("[feedback_scanner] working tree is not clean — skipping this run "
              "so an in-progress human edit can't be misattributed to a ticket fix")
        return

    conn = db.get_conn()
    tickets = [dict(t) for t in conn.execute(
        "SELECT * FROM feedback_tickets WHERE status = 'open'"
    ).fetchall()]
    print(f"[feedback_scanner] {len(tickets)} open ticket(s)")
    for ticket in tickets:
        if not _repo_is_clean():
            print("[feedback_scanner] working tree became dirty outside our control — stopping early")
            break
        print(f"[feedback_scanner] processing #{ticket['id']}: {ticket['title']}")
        try:
            process_ticket(conn, ticket)
        except Exception as e:
            print(f"[feedback_scanner] error on #{ticket['id']}: {e}")
    conn.close()


if __name__ == "__main__":
    main()
