import unittest

from diagnostics import diag_init, diag_mark_i2c_err, diag_mark_recover, diag_compute, diag_allow_scd_restart


class DiagnosticsTests(unittest.TestCase):
    def test_score_window(self):
        st = diag_init(0, max_events=50)
        now = 1000
        diag_mark_i2c_err(st, now, "sensor", 1)
        diag_mark_i2c_err(st, now + 1, "oled", 1)
        diag_mark_recover(st, now + 2, 2)
        out = diag_compute(st, now + 3, {"window_ms": 60000, "bad_score": 3, "i2c_kind": "i2c_err"})
        self.assertTrue(out["power_bad"])
        self.assertEqual(out["score"], 4)

    def test_scd_restart_guard(self):
        st = diag_init(0, max_events=10)
        now = 0
        ok, _ = diag_allow_scd_restart(st, now, 300000, 2)
        self.assertTrue(ok)
        ok, _ = diag_allow_scd_restart(st, now + 1, 300000, 2)
        self.assertTrue(ok)
        ok, _ = diag_allow_scd_restart(st, now + 2, 300000, 2)
        self.assertFalse(ok)
        self.assertTrue(st["runtime_degraded"])


if __name__ == "__main__":
    unittest.main()
