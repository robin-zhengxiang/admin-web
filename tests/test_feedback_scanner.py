import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import api_feedback
import db
import feedback_scanner
from tests.helpers import fake_session


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


class FeedbackScannerGitAttributionTests(unittest.TestCase):
    """Regression coverage for the real bug this project hit: an earlier version of
    _commit_if_changed() looked at whole-repo dirtiness instead of this-ticket's own
    diff, so a human's unrelated uncommitted work (or an earlier ticket in the same
    scan) got swept into a commit falsely labeled as an automated fix for a different
    ticket. Fixed by (1) refusing to run at all if the tree isn't clean at scan start,
    and (2) scoping "did this ticket change anything" to a HEAD-before/HEAD-after
    comparison recorded right before that ticket's own Claude call.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="admin-web-scanner-test-repo-")
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("initial\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "initial commit")

        self.db_dir = tempfile.mkdtemp(prefix="admin-web-scanner-test-db-")
        self.db_path = os.path.join(self.db_dir, "usage.db")

        mock.patch.object(feedback_scanner, "ADMIN_WEB_DIR", self.repo).start()
        mock.patch.object(db, "DB_PATH", self.db_path).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.repo, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.db_dir, ignore_errors=True))
        db.init_db()

    def _head(self):
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def _create_ticket(self, title, description):
        r = api_feedback.create_ticket(None, {}, {"title": title, "description": description}, fake_session("alice"), None)
        return r["id"]

    def _ticket_status(self, ticket_id):
        conn = db.get_conn()
        row = conn.execute("SELECT status FROM feedback_tickets WHERE id = ?", (ticket_id,)).fetchone()
        conn.close()
        return row["status"]

    # --- the actual regression: two tickets in one run, only one should get credit ---

    def test_only_the_ticket_that_actually_changed_files_gets_resolved(self):
        fixable_id = self._create_ticket("fixable", "please fix the typo")
        vague_id = self._create_ticket("vague", "site feels slow sometimes")

        def fake_run_claude(prompt, resume_session_id=None, timeout=600):
            if "fixable" in prompt:
                with open(os.path.join(self.repo, "fixed.txt"), "w") as f:
                    f.write("fixed\n")
                return "Fixed the typo in fixed.txt", "sess-fix", None
            return "Can you say more about when it's slow?", "sess-vague", None

        head_before = self._head()
        with mock.patch.object(feedback_scanner, "_run_claude", side_effect=fake_run_claude):
            feedback_scanner.main()
        head_after = self._head()

        self.assertEqual(self._ticket_status(fixable_id), "resolved")
        self.assertEqual(self._ticket_status(vague_id), "needs_input")
        # exactly one commit was made across the whole run — not one per ticket
        count = _git(self.repo, "rev-list", "--count", f"{head_before}..{head_after}").stdout.strip()
        self.assertEqual(count, "1")

    def test_dirty_working_tree_at_scan_start_skips_everything(self):
        ticket_id = self._create_ticket("fixable", "please fix the typo")
        with open(os.path.join(self.repo, "unrelated_human_edit.txt"), "w") as f:
            f.write("someone's in-progress work\n")

        def fake_run_claude(prompt, resume_session_id=None, timeout=600):
            with open(os.path.join(self.repo, "fixed.txt"), "w") as f:
                f.write("should never get here\n")
            return "Fixed it", "sess-1", None

        head_before = self._head()
        with mock.patch.object(feedback_scanner, "_run_claude", side_effect=fake_run_claude) as run_mock:
            feedback_scanner.main()

        run_mock.assert_not_called()
        self.assertEqual(self._ticket_status(ticket_id), "open")  # untouched
        self.assertEqual(self._head(), head_before)  # no commit happened
        self.assertTrue(os.path.exists(os.path.join(self.repo, "unrelated_human_edit.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "fixed.txt")))

    def test_ticket_that_causes_no_file_change_is_needs_input_with_no_commit(self):
        ticket_id = self._create_ticket("vague", "site feels slow sometimes")

        def fake_run_claude(prompt, resume_session_id=None, timeout=600):
            return "I need more detail to investigate this.", "sess-vague", None

        head_before = self._head()
        with mock.patch.object(feedback_scanner, "_run_claude", side_effect=fake_run_claude):
            feedback_scanner.main()

        self.assertEqual(self._ticket_status(ticket_id), "needs_input")
        self.assertEqual(self._head(), head_before)

    def test_agent_committing_itself_is_still_detected_as_a_change(self):
        # Claude's own tool permissions don't block `git commit` — if it commits mid-turn
        # instead of leaving the tree dirty, we must still recognize that as "changed"
        # rather than double-committing or missing it because `git status` looks clean.
        ticket_id = self._create_ticket("fixable", "please fix the typo")

        def fake_run_claude(prompt, resume_session_id=None, timeout=600):
            with open(os.path.join(self.repo, "fixed.txt"), "w") as f:
                f.write("fixed\n")
            _git(self.repo, "add", "-A")
            _git(self.repo, "commit", "-q", "-m", "agent's own commit")
            return "Fixed the typo and committed it myself", "sess-fix", None

        head_before = self._head()
        with mock.patch.object(feedback_scanner, "_run_claude", side_effect=fake_run_claude):
            feedback_scanner.main()
        head_after = self._head()

        self.assertEqual(self._ticket_status(ticket_id), "resolved")
        count = _git(self.repo, "rev-list", "--count", f"{head_before}..{head_after}").stdout.strip()
        self.assertEqual(count, "1")  # the agent's own commit, not a second one from the scanner

    def test_claude_error_marks_needs_input_without_touching_git(self):
        ticket_id = self._create_ticket("whatever", "some report")

        def fake_run_claude(prompt, resume_session_id=None, timeout=600):
            return None, "sess-err", "CLI exited with code 1"

        head_before = self._head()
        with mock.patch.object(feedback_scanner, "_run_claude", side_effect=fake_run_claude):
            feedback_scanner.main()

        self.assertEqual(self._ticket_status(ticket_id), "needs_input")
        self.assertEqual(self._head(), head_before)


if __name__ == "__main__":
    unittest.main()
