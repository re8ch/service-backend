import unittest

from controller import canonical_members, permission


class ControllerTests(unittest.TestCase):
    def test_permission_mapping_and_admin_roles(self):
        items = [
            {"login": "Woomou", "role_name": "admin"},
            {"login": "friend", "role_name": "read"},
            {"login": "pending", "role_name": "none"},
        ]
        members = canonical_members(items, {"woomou": "hliu74"})
        self.assertTrue(members["hliu74"]["admin"])
        self.assertTrue(members["friend"]["authorized"])
        self.assertNotIn("pending", members)

    def test_legacy_github_permissions(self):
        self.assertEqual(permission({"permissions": {"push": True}}), "write")


if __name__ == "__main__":
    unittest.main()
