import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import FakeMatch, fake_session, make_fake_user

import api_skills
import users
from routes import ApiError


def _write_skill(home, name, description="a test skill"):
    skill_dir = os.path.join(home, ".claude", "skills", name)
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {description}\n---\n\n# body\n")


class ApiSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin-web-test-")
        self.alice = make_fake_user(self.tmp, "alice", uid=501)
        self.bob = make_fake_user(self.tmp, "bob", uid=502)
        mock.patch.object(users, "list_local_users", return_value=[self.alice, self.bob]).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

        _write_skill(self.alice["home"], "alice-skill", "does alice things")
        _write_skill(self.bob["home"], "bob-skill", "does bob things")

    def test_list_skills_across_users_with_default_state(self):
        result = api_skills.list_skills(None, {}, {}, fake_session("alice"), None)
        by_name = {s["name"]: s for s in result["skills"]}
        self.assertEqual(by_name["alice-skill"]["owner_user"], "alice")
        self.assertEqual(by_name["alice-skill"]["state"], "default")
        self.assertEqual(by_name["bob-skill"]["owner_user"], "bob")

    def test_list_skills_reflects_existing_override(self):
        settings_path = os.path.join(self.alice["home"], ".claude", "settings.json")
        with open(settings_path, "w") as f:
            json.dump({"skillOverrides": {"alice-skill": "off"}}, f)

        result = api_skills.list_skills(None, {}, {}, fake_session("alice"), None)
        by_name = {s["name"]: s for s in result["skills"]}
        self.assertEqual(by_name["alice-skill"]["state"], "off")

    def test_set_state_requires_matching_owner(self):
        match = FakeMatch({"owner_user": "bob", "name": "bob-skill"})
        with self.assertRaises(ApiError) as ctx:
            api_skills.set_skill_state(match, {}, {"state": "off"}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 403)

    def test_set_state_rejects_invalid_state(self):
        match = FakeMatch({"owner_user": "alice", "name": "alice-skill"})
        with self.assertRaises(ApiError) as ctx:
            api_skills.set_skill_state(match, {}, {"state": "not-a-real-state"}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)

    def test_set_state_writes_and_clears_override(self):
        match = FakeMatch({"owner_user": "alice", "name": "alice-skill"})
        api_skills.set_skill_state(match, {}, {"state": "off"}, fake_session("alice"), None)

        settings_path = os.path.join(self.alice["home"], ".claude", "settings.json")
        with open(settings_path) as f:
            self.assertEqual(json.load(f)["skillOverrides"], {"alice-skill": "off"})

        api_skills.set_skill_state(match, {}, {"state": "default"}, fake_session("alice"), None)
        with open(settings_path) as f:
            self.assertEqual(json.load(f)["skillOverrides"], {})

    def test_content_round_trip_requires_owner_to_write(self):
        get_match = FakeMatch({"owner_user": "alice", "name": "alice-skill"})
        content = api_skills.get_skill_content(get_match, {}, {}, fake_session("bob"), None)
        self.assertIn("does alice things", content["content"])

        with self.assertRaises(ApiError) as ctx:
            api_skills.put_skill_content(get_match, {}, {"content": "hacked"}, fake_session("bob"), None)
        self.assertEqual(ctx.exception.status, 403)

        api_skills.put_skill_content(get_match, {}, {"content": "# new content\n"}, fake_session("alice"), None)
        updated = api_skills.get_skill_content(get_match, {}, {}, fake_session("bob"), None)
        self.assertEqual(updated["content"], "# new content\n")

    def test_unknown_skill_is_404(self):
        match = FakeMatch({"owner_user": "alice", "name": "does-not-exist"})
        with self.assertRaises(ApiError) as ctx:
            api_skills.get_skill_content(match, {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 404)

    def test_path_traversal_in_skill_name_is_rejected(self):
        match = FakeMatch({"owner_user": "alice", "name": "../../etc"})
        with self.assertRaises(ApiError) as ctx:
            api_skills.get_skill_content(match, {}, {}, fake_session("alice"), None)
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
