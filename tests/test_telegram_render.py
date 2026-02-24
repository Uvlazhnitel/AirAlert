import unittest

from telegram_render import render_health_card


class TelegramRenderTests(unittest.TestCase):
    def test_health_card_contains_core_fields(self):
        txt = render_health_card(
            3661,
            {
                "mode": "NORMAL",
                "power_bad": False,
                "score": 2,
                "err_rate_per_min": 1.25,
                "window_ms": 60000,
                "i2c_err_total": 4,
                "sensor_err_total": 2,
                "oled_init_err_total": 1,
                "recover_total": 1,
                "wifi_reconnect_total": 0,
                "tg_send_err_total": 0,
                "last_recover_age_s": "10",
                "last_i2c_err_age_s": "3",
                "last_power_bad_age_s": "-",
                "time_sync_last_ok_age_s": "100",
                "time_synced": True,
                "bus_mode": "SHARED",
                "bus_freq_hz": 20000,
            },
        )
        self.assertIn("System Health", txt)
        self.assertIn("Mode:", txt)
        self.assertIn("Bus:", txt)
        self.assertIn("Err rate:", txt)


if __name__ == "__main__":
    unittest.main()
