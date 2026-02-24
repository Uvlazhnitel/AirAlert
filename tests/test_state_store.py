import unittest

from state_store import validate_settings, apply_state_defaults


class StateStoreTests(unittest.TestCase):
    def test_validate_settings_ok(self):
        ok, _ = validate_settings(800, 1500, 20)
        self.assertTrue(ok)

    def test_validate_settings_high_gap(self):
        ok, msg = validate_settings(1000, 1100, 20)
        self.assertFalse(ok)
        self.assertIn("WARN+200", msg)

    def test_apply_defaults_repairs_invalid(self):
        st = {"warn_on": 1300, "high_on": 1200, "remind_min": 1}
        out = apply_state_defaults(st)
        self.assertGreaterEqual(out["high_on"], out["warn_on"] + 200)
        self.assertGreaterEqual(out["remind_min"], 5)


if __name__ == "__main__":
    unittest.main()
