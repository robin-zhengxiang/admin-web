import os
import plistlib
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import FakeMatch, fake_session, make_fake_user

import api_tasks
import users
from routes import ApiError


def _write_plist(home, label, schedule=None):
    path = os.path.join(home, "Library", "LaunchAgents", label + ".plist")
    data = {
        "Label": label,
        "ProgramArguments": ["/usr/bin/true"],
        "RunAtLoad": False,
    }
    if schedule is not None:
        data["StartCalendarInterval"] = schedule
    with open(path, "wb") as f:
        plistlib.dump(data, f)
    return path


def _fake_completed(stdout="", returncode=0):
    result = mock.Mock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class ApiTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin-web-test-")
        self.alice = make_fake_user(self.tmp, "alice", uid=501)
        self.bob = make_fake_user(self.tmp, "bob", uid=502)
        mock.patch.object(users, "list_local_users", return_value=[self.alice, self.bob]).start()
        mock.patch.object(api_tasks, "_current_uid", return_value=501).start()  # pretend we run as alice
        mock.patch.object(api_tasks, "_registered_tasks_for", return_value={}).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_list_tasks_reports_running_and_disabled(self):
        _write_plist(self.alice["home"], "com.alice.scheduled-thing", schedule={"Hour": 2, "Minute": 0})
        _write_plist(self.bob["home"], "com.bob.daemon-thing")

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["launchctl", "list"]:
                return _fake_completed(stdout="PID\tStatus\tLabel\n123\t0\tcom.alice.scheduled-thing\n")
            if cmd[:3] == ["launchctl", "asuser", "502"]:
                return _fake_completed(stdout="PID\tStatus\tLabel\n-\t0\tcom.bob.daemon-thing\n")
            return _fake_completed()

        with mock.patch("api_tasks.subprocess.run", side_effect=fake_run):
            result = api_tasks.list_tasks(None, {}, {}, fake_session("alice"), None)

        by_label = {t["label"]: t for t in result["tasks"]}
        self.assertEqual(by_label["com.alice.scheduled-thing"]["status"], "running")
        self.assertEqual(by_label["com.alice.scheduled-thing"]["pid"], "123")
        self.assertEqual(by_label["com.alice.scheduled-thing"]["type"], "scheduled")
        self.assertEqual(by_label["com.bob.daemon-thing"]["status"], "enabled")
        self.assertEqual(by_label["com.bob.daemon-thing"]["owner_user"], "bob")

    def test_enable_requires_matching_owner(self):
        _write_plist(self.bob["home"], "com.bob.daemon-thing")
        match = FakeMatch({"owner_user": "bob", "label": "com.bob.daemon-thing"})
        with self.assertRaises(ApiError) as ctx:
            api_tasks.enable_task(match, {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 403)

    def test_enable_uses_asuser_for_other_user(self):
        _write_plist(self.bob["home"], "com.bob.daemon-thing")
        match = FakeMatch({"owner_user": "bob", "label": "com.bob.daemon-thing"})
        with mock.patch("api_tasks.subprocess.run", return_value=_fake_completed()) as run:
            result = api_tasks.enable_task(match, {}, {}, fake_session("bob"), None)
        self.assertTrue(result["ok"])
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], ["launchctl", "asuser", "502"])
        self.assertIn("load", cmd)

    def test_enable_skips_asuser_for_current_user(self):
        _write_plist(self.alice["home"], "com.alice.thing")
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.thing"})
        with mock.patch("api_tasks.subprocess.run", return_value=_fake_completed()) as run:
            api_tasks.enable_task(match, {}, {}, fake_session("alice"), None)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "launchctl")
        self.assertNotIn("asuser", cmd)

    def test_task_not_found_is_404(self):
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.does-not-exist"})
        with self.assertRaises(ApiError) as ctx:
            api_tasks.enable_task(match, {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 404)

    def test_set_schedule_updates_plist_and_reloads(self):
        _write_plist(self.alice["home"], "com.alice.scheduled-thing", schedule={"Hour": 2, "Minute": 0})
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.scheduled-thing"})
        with mock.patch("api_tasks.subprocess.run", return_value=_fake_completed()) as run:
            result = api_tasks.set_schedule(match, {}, {"hour": 9, "minute": 30}, fake_session("alice"), None)
        self.assertEqual(result["schedule"], {"Hour": 9, "Minute": 30})

        plist_path = os.path.join(self.alice["home"], "Library", "LaunchAgents", "com.alice.scheduled-thing.plist")
        with open(plist_path, "rb") as f:
            self.assertEqual(plistlib.load(f)["StartCalendarInterval"], {"Hour": 9, "Minute": 30})
        # unload + load, both without asuser since it's the current (alice) user
        self.assertEqual(run.call_count, 2)

    def test_set_schedule_rejects_non_scheduled_task(self):
        _write_plist(self.alice["home"], "com.alice.daemon-thing")  # no StartCalendarInterval
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.daemon-thing"})
        with self.assertRaises(ApiError) as ctx:
            api_tasks.set_schedule(match, {}, {"hour": 9, "minute": 30}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)

    def test_set_schedule_requires_hour_and_minute(self):
        _write_plist(self.alice["home"], "com.alice.scheduled-thing", schedule={"Hour": 2, "Minute": 0})
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.scheduled-thing"})
        with self.assertRaises(ApiError) as ctx:
            api_tasks.set_schedule(match, {}, {"hour": 9}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)

    def test_logs_without_registered_entry_returns_note(self):
        _write_plist(self.alice["home"], "com.alice.thing")
        match = FakeMatch({"owner_user": "alice", "label": "com.alice.thing"})
        result = api_tasks.task_logs(match, {}, {}, fake_session("alice"), None)
        self.assertEqual(result["lines"], [])
        self.assertIn("note", result)


if __name__ == "__main__":
    unittest.main()
