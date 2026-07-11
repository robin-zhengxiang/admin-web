import time
import unittest
from unittest import mock

from tests.helpers import FakeMatch

import auth
import users
from routes import ApiError, ResponseHelper


class VerifyPasswordTests(unittest.TestCase):
    def test_unknown_user_never_authenticates(self):
        self.assertFalse(auth.verify_password("no-such-local-user-xyz", "whatever"))

    def test_wrong_password_for_real_user_fails(self):
        real_users = users.list_local_users()
        if not real_users:
            self.skipTest("no local users found on this machine")
        self.assertFalse(auth.verify_password(real_users[0]["username"], "definitely-wrong-xyz-123"))


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        auth._sessions.clear()

    def test_create_and_get_session(self):
        sid = auth.create_session("alice", 501)
        session = auth.get_session(sid)
        self.assertEqual(session["username"], "alice")
        self.assertEqual(session["uid"], 501)

    def test_unknown_session_id_returns_none(self):
        self.assertIsNone(auth.get_session("not-a-real-session-id"))
        self.assertIsNone(auth.get_session(None))
        self.assertIsNone(auth.get_session(""))

    def test_expired_session_is_evicted(self):
        sid = auth.create_session("alice", 501)
        auth._sessions[sid]["expires"] = time.time() - 1
        self.assertIsNone(auth.get_session(sid))
        self.assertNotIn(sid, auth._sessions)

    def test_delete_session(self):
        sid = auth.create_session("alice", 501)
        auth.delete_session(sid)
        self.assertIsNone(auth.get_session(sid))


class LoginRouteTests(unittest.TestCase):
    def setUp(self):
        auth._sessions.clear()

    def test_missing_fields_400(self):
        with self.assertRaises(ApiError) as ctx:
            auth.login(FakeMatch({}), {}, {}, None, ResponseHelper())
        self.assertEqual(ctx.exception.status, 400)

    def test_unknown_user_401(self):
        with mock.patch.object(users, "get_user", return_value=None):
            with self.assertRaises(ApiError) as ctx:
                auth.login(FakeMatch({}), {}, {"username": "ghost", "password": "x"}, None, ResponseHelper())
        self.assertEqual(ctx.exception.status, 401)

    def test_wrong_password_401(self):
        fake_user = {"username": "alice", "uid": 501, "gid": 20, "home": "/Users/alice"}
        with mock.patch.object(users, "get_user", return_value=fake_user), \
             mock.patch.object(auth, "verify_password", return_value=False):
            with self.assertRaises(ApiError) as ctx:
                auth.login(FakeMatch({}), {}, {"username": "alice", "password": "wrong"}, None, ResponseHelper())
        self.assertEqual(ctx.exception.status, 401)

    def test_successful_login_sets_cookie_and_session(self):
        fake_user = {"username": "alice", "uid": 501, "gid": 20, "home": "/Users/alice"}
        resp = ResponseHelper()
        with mock.patch.object(users, "get_user", return_value=fake_user), \
             mock.patch.object(auth, "verify_password", return_value=True):
            result = auth.login(FakeMatch({}), {}, {"username": "alice", "password": "right"}, None, resp)
        self.assertEqual(result, {"ok": True, "username": "alice"})
        self.assertEqual(len(resp.extra_headers), 1)
        self.assertEqual(resp.extra_headers[0][0], "Set-Cookie")
        self.assertIn(auth.SESSION_COOKIE_NAME, resp.extra_headers[0][1])
        self.assertEqual(len(auth._sessions), 1)


class LogoutAndMeTests(unittest.TestCase):
    def setUp(self):
        auth._sessions.clear()

    def test_logout_clears_the_calling_session_only(self):
        sid_a = auth.create_session("alice", 501)
        auth.create_session("bob", 502)
        session_a = auth.get_session(sid_a)
        resp = ResponseHelper()
        result = auth.logout(FakeMatch({}), {}, {}, session_a, resp)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(auth._sessions), 1)
        self.assertIsNone(auth.get_session(sid_a))

    def test_me_returns_session_identity(self):
        session = {"username": "alice", "uid": 501}
        result = auth.me(FakeMatch({}), {}, {}, session, None)
        self.assertEqual(result, {"username": "alice", "uid": 501})


if __name__ == "__main__":
    unittest.main()
