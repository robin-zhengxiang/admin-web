import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import FakeMatch, fake_session

import api_feedback
import db
from routes import ApiError


class ApiFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin-web-test-")
        self.db_path = os.path.join(self.tmp, "usage.db")
        mock.patch.object(db, "DB_PATH", self.db_path).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        db.init_db()

    def test_create_ticket_requires_title_and_description(self):
        with self.assertRaises(ApiError) as ctx:
            api_feedback.create_ticket(None, {}, {"title": "only a title"}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)

    def test_create_and_list_ticket(self):
        r = api_feedback.create_ticket(
            None, {}, {"title": "bug!", "description": "it broke", "page": "/tasks"},
            fake_session("alice"), None,
        )
        self.assertTrue(r["ok"])
        tid = r["id"]

        listing = api_feedback.list_tickets(None, {}, {}, fake_session("bob"), None)
        self.assertEqual(len(listing["tickets"]), 1)
        self.assertEqual(listing["tickets"][0]["owner_user"], "alice")
        self.assertEqual(listing["tickets"][0]["status"], "open")

        detail = api_feedback.ticket_detail(FakeMatch({"ticket_id": str(tid)}), {}, {}, fake_session("bob"), None)
        self.assertEqual(len(detail["messages"]), 1)
        self.assertIn("it broke", detail["messages"][0]["content"])
        self.assertIn("/tasks", detail["messages"][0]["content"])

    def test_tickets_are_visible_to_any_logged_in_user(self):
        # feedback is shared across the household, unlike skills/tasks — no owner check on read
        api_feedback.create_ticket(None, {}, {"title": "t", "description": "d"}, fake_session("alice"), None)
        as_bob = api_feedback.list_tickets(None, {}, {}, fake_session("bob"), None)
        self.assertEqual(len(as_bob["tickets"]), 1)

    def test_ticket_detail_404_for_unknown_id(self):
        with self.assertRaises(ApiError) as ctx:
            api_feedback.ticket_detail(FakeMatch({"ticket_id": "999"}), {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 404)

    def test_reply_resets_status_to_open_and_appends_message(self):
        r = api_feedback.create_ticket(None, {}, {"title": "t", "description": "d"}, fake_session("alice"), None)
        tid = r["id"]

        conn = db.get_conn()
        conn.execute("UPDATE feedback_tickets SET status = 'needs_input' WHERE id = ?", (tid,))
        conn.commit()
        conn.close()

        api_feedback.reply_ticket(FakeMatch({"ticket_id": str(tid)}), {}, {"content": "more info"}, fake_session("alice"), None)

        detail = api_feedback.ticket_detail(FakeMatch({"ticket_id": str(tid)}), {}, {}, fake_session("alice"), None)
        self.assertEqual(detail["ticket"]["status"], "open")
        self.assertEqual(len(detail["messages"]), 2)
        self.assertEqual(detail["messages"][1]["content"], "more info")

    def test_reply_requires_nonempty_content(self):
        r = api_feedback.create_ticket(None, {}, {"title": "t", "description": "d"}, fake_session("alice"), None)
        with self.assertRaises(ApiError) as ctx:
            api_feedback.reply_ticket(FakeMatch({"ticket_id": str(r["id"])}), {}, {"content": "   "}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)

    def test_reply_to_unknown_ticket_404(self):
        with self.assertRaises(ApiError) as ctx:
            api_feedback.reply_ticket(FakeMatch({"ticket_id": "999"}), {}, {"content": "hi"}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
