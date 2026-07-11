import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import FakeMatch, fake_session, make_fake_user

import api_tokens
import db
import users


class RangeCutoffTests(unittest.TestCase):
    def test_known_ranges_and_all(self):
        self.assertIsNone(api_tokens._range_cutoff("all"))
        self.assertIsNotNone(api_tokens._range_cutoff("7d"))
        self.assertIsNotNone(api_tokens._range_cutoff("30d"))
        self.assertIsNotNone(api_tokens._range_cutoff("1y"))

    def test_1y_cutoff_is_far_earlier_than_30d(self):
        cutoff_30d = api_tokens._range_cutoff("30d")
        cutoff_1y = api_tokens._range_cutoff("1y")
        self.assertLess(cutoff_1y, cutoff_30d)


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.pricing = {
            "_default": {"input": 3.0, "output": 15.0, "cache_write_5m": 3.75, "cache_read": 0.30},
            "models": {
                "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write_5m": 6.25, "cache_read": 0.50},
            },
        }

    def test_exact_and_prefix_model_match(self):
        self.assertEqual(api_tokens._price_for_model(self.pricing, "claude-opus-4-8")["input"], 5.0)
        # dated variant should prefix-match the bare model id
        self.assertEqual(api_tokens._price_for_model(self.pricing, "claude-opus-4-8-20260101")["input"], 5.0)

    def test_unknown_model_falls_back_to_default(self):
        self.assertEqual(api_tokens._price_for_model(self.pricing, "some-unknown-model")["input"], 3.0)
        self.assertEqual(api_tokens._price_for_model(self.pricing, None)["input"], 3.0)

    def test_synthetic_model_costs_nothing(self):
        cost = api_tokens._estimate_cost(self.pricing, "<synthetic>", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
        self.assertEqual(cost, 0.0)

    def test_cost_math(self):
        # 1M input + 1M output + 1M cache_read + 1M cache_creation at opus-4-8 rates
        cost = api_tokens._estimate_cost(self.pricing, "claude-opus-4-8", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 5.0 + 25.0 + 0.50 + 6.25)


class OverviewAndSessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin-web-test-")
        self.alice = make_fake_user(self.tmp, "alice", uid=501)
        self.bob = make_fake_user(self.tmp, "bob", uid=502)
        self.db_path = os.path.join(self.tmp, "usage.db")

        self.pricing_path = os.path.join(self.tmp, "pricing.json")
        with open(self.pricing_path, "w") as f:
            json.dump({
                "_default": {"input": 3.0, "output": 15.0, "cache_write_5m": 3.75, "cache_read": 0.30},
                "models": {},
            }, f)

        mock.patch.object(db, "DB_PATH", self.db_path).start()
        mock.patch.object(users, "list_local_users", return_value=[self.alice, self.bob]).start()
        mock.patch.object(api_tokens, "PRICING_PATH", self.pricing_path).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        db.init_db()

        conn = db.get_conn()
        conn.executescript(f"""
            INSERT INTO sessions(session_id, owner_user, title, first_ts, last_ts)
            VALUES ('s1', 'alice', 'Alice session', '2026-01-01T00:00:00.000Z', '2026-01-01T00:05:00.000Z');
            INSERT INTO sessions(session_id, owner_user, title, first_ts, last_ts)
            VALUES ('s2', 'bob', 'Bob session', '2026-01-01T00:00:00.000Z', '2026-01-01T00:05:00.000Z');
        """)
        conn.execute(
            """INSERT INTO usage_events(uuid, session_id, owner_user, project, ts, model, input_tokens, output_tokens,
                                         cache_read_tokens, cache_creation_tokens, is_sidechain)
               VALUES ('e1', 's1', 'alice', '/proj-a', '2026-01-01T00:00:00.000Z', 'claude-opus-4-8', 100, 200, 0, 0, 0)""",
        )
        conn.execute(
            """INSERT INTO usage_events(uuid, session_id, owner_user, project, ts, model, input_tokens, output_tokens,
                                         cache_read_tokens, cache_creation_tokens, is_sidechain)
               VALUES ('e2', 's2', 'bob', '/proj-b', '2026-01-01T00:00:00.000Z', 'claude-opus-4-8', 300, 400, 0, 0, 0)""",
        )
        conn.commit()
        conn.close()

    def test_overview_all_users_totals(self):
        result = api_tokens.overview(None, {"range": ["all"]}, {}, fake_session("alice"), None)
        self.assertEqual(result["totals"]["input"], 400)
        self.assertEqual(result["totals"]["output"], 600)
        by_user = {r["owner_user"]: r for r in result["by_user"]}
        self.assertEqual(by_user["alice"]["input"], 100)
        self.assertEqual(by_user["bob"]["input"], 300)

    def test_overview_filtered_by_user(self):
        result = api_tokens.overview(None, {"range": ["all"], "user": ["bob"]}, {}, fake_session("alice"), None)
        self.assertEqual(result["totals"]["input"], 300)
        self.assertEqual(len(result["by_user"]), 1)

    def test_list_sessions_sorted_by_cost(self):
        result = api_tokens.list_sessions(None, {"range": ["all"], "sort": ["cost"]}, {}, fake_session("alice"), None)
        sessions = result["sessions"]
        self.assertEqual(len(sessions), 2)
        # bob's session has more tokens -> higher cost -> sorts first
        self.assertEqual(sessions[0]["session_id"], "s2")

    def test_session_detail_not_found(self):
        from routes import ApiError
        with self.assertRaises(ApiError) as ctx:
            api_tokens.session_detail(FakeMatch({"session_id": "does-not-exist"}), {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 404)

    def test_session_detail_splits_main_and_sidechain(self):
        conn = db.get_conn()
        conn.execute(
            """INSERT INTO usage_events(uuid, session_id, owner_user, ts, model, input_tokens, output_tokens,
                                         cache_read_tokens, cache_creation_tokens, is_sidechain, parent_uuid, tools)
               VALUES ('e3', 's1', 'alice', '2026-01-01T00:01:00.000Z', 'claude-opus-4-8', 5, 5, 0, 0, 1, 'e1', 'Bash,Read')""",
        )
        conn.commit()
        conn.close()

        detail = api_tokens.session_detail(FakeMatch({"session_id": "s1"}), {}, {}, fake_session("alice"), None)
        self.assertEqual(len(detail["main_thread"]), 1)
        self.assertEqual(len(detail["sidechain"]), 1)
        self.assertEqual(detail["sidechain"][0]["tools"], ["Bash", "Read"])


if __name__ == "__main__":
    unittest.main()
