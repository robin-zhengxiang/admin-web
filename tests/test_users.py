import unittest
from unittest import mock

from tests.helpers import FakeMatch

import users


class ListLocalUsersTests(unittest.TestCase):
    def test_returns_only_real_interactive_accounts(self):
        result = users.list_local_users()
        self.assertIsInstance(result, list)
        for u in result:
            self.assertIn("username", u)
            self.assertGreaterEqual(u["uid"], users.MIN_UID)
            self.assertTrue(u["home"].startswith("/Users/"))
            self.assertNotIn(u["username"].lower(), users.EXCLUDED_USERNAMES)

    def test_excludes_system_and_guest_accounts_by_construction(self):
        # A fake pwd entry for "guest" or a sub-500 uid must never survive the filter,
        # regardless of what the real machine happens to have.
        for banned in ("guest", "root", "daemon", "nobody"):
            self.assertIn(banned, users.EXCLUDED_USERNAMES)


class GetUserHelpersTests(unittest.TestCase):
    def setUp(self):
        self.fake_users = [
            {"username": "alice", "uid": 501, "gid": 20, "home": "/Users/alice"},
            {"username": "bob", "uid": 502, "gid": 20, "home": "/Users/bob"},
        ]

    def test_get_user_by_name(self):
        with mock.patch.object(users, "list_local_users", return_value=self.fake_users):
            self.assertEqual(users.get_user("bob")["uid"], 502)
            self.assertIsNone(users.get_user("nobody-such-user"))

    def test_get_user_by_uid(self):
        with mock.patch.object(users, "list_local_users", return_value=self.fake_users):
            self.assertEqual(users.get_user_by_uid(501)["username"], "alice")
            self.assertIsNone(users.get_user_by_uid(9999))


class UsersRouteTests(unittest.TestCase):
    def test_list_users_route_shape(self):
        fake_users = [{"username": "alice", "uid": 501, "gid": 20, "home": "/Users/alice"}]
        with mock.patch.object(users, "list_local_users", return_value=fake_users):
            result = users.list_users_route(FakeMatch({}), {}, {}, None, None)
        self.assertEqual(result, {"users": [{"username": "alice", "uid": 501}]})


if __name__ == "__main__":
    unittest.main()
