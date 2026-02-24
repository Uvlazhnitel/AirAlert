#!/usr/bin/env python3
"""
Host-side dry-run smoke for core formatting/command plumbing without Telegram API calls.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from telegram_render import render_menu_section, render_health_card


def main():
    state = {"warn_on": 800, "high_on": 1500, "remind_min": 20}
    txt, _ = render_menu_section(
        "home",
        co2_raw=900, temp_raw=23.1, rh_raw=40.0,
        co2_f=880.0, temp_f=23.0, rh_f=39.8,
        sample_age_s="1",
        sensor_ok=True, wifi_ok=True, uptime_s=123, remind_ms=20 * 60 * 1000,
        scd_scan="0x62", oled_scan="0x3c", state=state,
        local_time_txt="2026-02-24 10:00", time_synced=True, time_sync_error="", quiet_now=False
    )
    assert "Welcome to Air Monitor" in txt

    htxt = render_health_card(
        1234,
        {
            "mode": "NORMAL",
            "power_bad": False,
            "score": 0,
            "err_rate_per_min": 0.0,
            "window_ms": 60000,
            "i2c_err_total": 0,
            "sensor_err_total": 0,
            "oled_init_err_total": 0,
            "recover_total": 0,
            "wifi_reconnect_total": 0,
            "tg_send_err_total": 0,
            "last_recover_age_s": "-",
            "last_i2c_err_age_s": "-",
            "last_power_bad_age_s": "-",
            "time_sync_last_ok_age_s": "-",
            "time_synced": True,
            "bus_mode": "SHARED",
            "bus_freq_hz": 20000,
        },
    )
    assert "System Health" in htxt
    print("smoke_dryrun: OK")


if __name__ == "__main__":
    main()
