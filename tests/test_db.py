import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.helpers import make_fake_user

import db
import users


def _line(**kwargs):
    return json.dumps(kwargs) + "\n"


class ScanOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin-web-test-")
        self.alice = make_fake_user(self.tmp, "alice", uid=501)
        self.db_path = os.path.join(self.tmp, "usage.db")

        self.db_patch = mock.patch.object(db, "DB_PATH", self.db_path)
        self.users_patch = mock.patch.object(users, "list_local_users", return_value=[self.alice])
        self.db_patch.start()
        self.users_patch.start()
        db.init_db()

        self.project_dir = os.path.join(self.alice["home"], ".claude", "projects", "testproj")
        os.makedirs(self.project_dir, exist_ok=True)
        self.session_file = os.path.join(self.project_dir, "sess1.jsonl")

    def tearDown(self):
        self.db_patch.stop()
        self.users_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, lines):
        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write("".join(lines))

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_full_parse_tags_owner_and_extracts_fields(self):
        self._write([
            _line(type="ai-title", aiTitle="测试标题", sessionId="sess1"),
            _line(type="user", sessionId="sess1", cwd="/Users/alice", timestamp="2026-01-01T00:00:00.000Z",
                  message={"role": "user", "content": "第一条问题"}),
            _line(type="assistant", sessionId="sess1", cwd="/Users/alice", timestamp="2026-01-01T00:00:01.000Z",
                  uuid="u1", parentUuid=None, isSidechain=False,
                  message={"model": "claude-opus-4-8",
                           "usage": {"input_tokens": 10, "output_tokens": 20,
                                     "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2},
                           "content": [{"type": "text", "text": "回答内容"}]}),
        ])

        processed = db.scan_once()
        self.assertEqual(processed, 3)

        conn = self._conn()
        session = conn.execute("SELECT * FROM sessions WHERE session_id = 'sess1'").fetchone()
        self.assertEqual(session["owner_user"], "alice")
        self.assertEqual(session["title"], "测试标题")
        self.assertEqual(session["first_user_message"], "第一条问题")

        event = conn.execute("SELECT * FROM usage_events WHERE uuid = 'u1'").fetchone()
        self.assertEqual(event["owner_user"], "alice")
        self.assertEqual(event["input_tokens"], 10)
        self.assertEqual(event["output_tokens"], 20)
        self.assertEqual(event["cache_read_tokens"], 5)
        self.assertEqual(event["cache_creation_tokens"], 2)
        self.assertEqual(event["text_preview"], "回答内容")
        conn.close()

    def test_rescan_does_not_reprocess_unchanged_file(self):
        self._write([_line(type="ai-title", aiTitle="t", sessionId="sess1")])
        self.assertEqual(db.scan_once(), 1)
        self.assertEqual(db.scan_once(), 0)  # nothing new, offset already at EOF

    def test_incremental_append_only_processes_new_lines(self):
        self._write([_line(type="ai-title", aiTitle="t", sessionId="sess1")])
        db.scan_once()
        self._write([_line(type="ai-title", aiTitle="t2", sessionId="sess1")])
        processed = db.scan_once()
        self.assertEqual(processed, 1)

        conn = self._conn()
        session = conn.execute("SELECT title FROM sessions WHERE session_id = 'sess1'").fetchone()
        self.assertEqual(session["title"], "t2")
        conn.close()

    def test_permission_error_on_one_file_does_not_crash_the_scan(self):
        self._write([_line(type="ai-title", aiTitle="t", sessionId="sess1")])
        with mock.patch("builtins.open", side_effect=PermissionError()):
            # _scan_file catches PermissionError internally; scan_once must not raise
            processed = db.scan_once()
        self.assertEqual(processed, 0)


if __name__ == "__main__":
    unittest.main()
