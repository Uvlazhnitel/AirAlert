from config import *


def _fmt_int(v):
    return "-" if v is None else str(int(round(v)))


def _fmt_1(v):
    return "-" if v is None else "{:.1f}".format(v)


def quality_label(co2, warn_on, high_on):
    if co2 < warn_on:
        return "GOOD"
    if co2 <= high_on:
        return "OK"
    return "HIGH"


def menu_keyboard_home():
    return {
        "inline_keyboard": [
            [{"text": "Status", "callback_data": "menu:status"},
             {"text": "Controls", "callback_data": "menu:controls"}],
            [{"text": "Settings", "callback_data": "menu:settings"},
             {"text": "Help", "callback_data": "menu:help"}],
            [{"text": "Refresh", "callback_data": "menu:refresh"}],
        ]
    }


def menu_keyboard_status():
    return {
        "inline_keyboard": [
            [{"text": "Details", "callback_data": "menu:details"},
             {"text": "Refresh", "callback_data": "menu:status"}],
            [{"text": "Back", "callback_data": "menu:home"}],
        ]
    }


def menu_keyboard_details():
    return {
        "inline_keyboard": [
            [{"text": "Back", "callback_data": "menu:status"},
             {"text": "Refresh", "callback_data": "menu:details"}],
        ]
    }


def menu_keyboard_controls():
    return {
        "inline_keyboard": [
            [{"text": "Preset Home", "callback_data": "cfg:preset:home"}],
            [{"text": "Back", "callback_data": "menu:home"}],
        ]
    }


def settings_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "WARN -50", "callback_data": "cfg:warn:-50"},
             {"text": "WARN +50", "callback_data": "cfg:warn:+50"}],
            [{"text": "HIGH -50", "callback_data": "cfg:high:-50"},
             {"text": "HIGH +50", "callback_data": "cfg:high:+50"}],
            [{"text": "REM -5", "callback_data": "cfg:remind:-5"},
             {"text": "REM +5", "callback_data": "cfg:remind:+5"}],
            [{"text": "Preset Home", "callback_data": "cfg:preset:home"}],
            [{"text": "Refresh", "callback_data": "menu:settings"}],
            [{"text": "Back", "callback_data": "menu:home"}],
        ]
    }


def thresholds_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "WARN -50", "callback_data": "thr:warn:-50"},
             {"text": "WARN +50", "callback_data": "thr:warn:+50"}],
            [{"text": "HIGH -50", "callback_data": "thr:high:-50"},
             {"text": "HIGH +50", "callback_data": "thr:high:+50"}],
            [{"text": "Preset Home", "callback_data": "thr:preset:home"}],
            [{"text": "Refresh", "callback_data": "menu:thresholds"}],
            [{"text": "Back", "callback_data": "menu:home"}],
        ]
    }


def menu_keyboard_help():
    return {"inline_keyboard": [[{"text": "Back", "callback_data": "menu:home"}]]}


def render_status_card(co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, warn_on, high_on):
    lvl = "-" if co2_f is None else quality_label(co2_f, warn_on, high_on)
    return (
        "🌿 Air Monitor\n"
        "CO2: {} ppm ({})\n"
        "Temp: {} C  RH: {} %\n"
        "Sample age: {} s\n"
        "Health: Sensor {} | Wi-Fi {}\n"
        "Tip: use /info for full diagnostics."
    ).format(
        _fmt_int(co2_f),
        lvl,
        _fmt_1(temp_f),
        _fmt_1(rh_f),
        sample_age_s,
        "OK" if sensor_ok else "ERR",
        "OK" if wifi_ok else "ERR",
    )


def render_details_card(
    co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
    sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan,
    warn_on, high_on, local_time_txt, time_synced, time_sync_error, quiet_now
):
    lvl = "-" if co2_f is None else quality_label(co2_f, warn_on, high_on)
    sync_txt = "OK" if time_synced else "ERR"
    sync_err = "-" if time_synced else (time_sync_error or "-")
    quiet_txt = "YES" if quiet_now else "NO"
    return (
        "🔎 System Details\n"
        "Wi-Fi: {} | Sensor: {}\n"
        "Local time: {} | Time sync: {}\n"
        "Time sync error: {}\n"
        "Level: {}\n"
        "Raw: CO2 {} | T {} C | RH {} %\n"
        "Filtered: CO2 {} | T {} C | RH {} %\n"
        "Sample age: {} s | Uptime: {} s\n"
        "Thresholds: WARN {} / HIGH {}\n"
        "Reminder: {} min | Quiet now: {}\n"
        "I2C SCD: {}\n"
        "I2C OLED: {}"
    ).format(
        "OK" if wifi_ok else "ERR",
        "OK" if sensor_ok else "ERR",
        local_time_txt,
        sync_txt,
        sync_err,
        lvl,
        _fmt_int(co2_raw),
        "-" if temp_raw is None else "{:.2f}".format(temp_raw),
        "-" if rh_raw is None else "{:.2f}".format(rh_raw),
        _fmt_int(co2_f),
        "-" if temp_f is None else "{:.2f}".format(temp_f),
        "-" if rh_f is None else "{:.2f}".format(rh_f),
        sample_age_s,
        uptime_s,
        warn_on,
        high_on,
        remind_ms // 60000,
        quiet_txt,
        scd_scan,
        oled_scan,
    )


def render_settings_card(warn_on, high_on, remind_min):
    return (
        "🛠 Settings\n"
        "WARN: {} ppm\n"
        "HIGH: {} ppm\n"
        "Reminder: {} min\n"
        "Quiet mode: {} {:02d}:00-{:02d}:00 (local)\n"
        "Rule: HIGH must be at least WARN + 200."
    ).format(
        warn_on,
        high_on,
        remind_min,
        "ON" if QUIET_ENABLE else "OFF",
        QUIET_START_H,
        QUIET_END_H
    )


def render_thresholds_card(warn_on, high_on):
    return (
        "🎯 Thresholds\n"
        "WARN: {} ppm\n"
        "HIGH: {} ppm\n"
        "Rule: HIGH must be at least WARN + 200."
    ).format(warn_on, high_on)


def render_help_card():
    return (
        "ℹ️ Help\n"
        "Inline buttons are disabled.\n"
        "Commands: /menu /status /info /health /diag /events /thresholds /settings /help\n"
        "/health -> power diagnostics\n"
        "/diag -> operational snapshot\n"
        "/events -> recent runtime events\n"
        "/thresholds -> WARN/HIGH\n"
        "/settings -> WARN/HIGH/REM\n"
        "Quiet mode follows local synced time."
    )


def render_controls_card():
    return (
        "⚙️ Controls\n"
        "Inline controls are currently disabled.\n"
        "Use /thresholds or /settings to adjust limits."
    )


def render_menu_home_card(co2_f, temp_f, rh_f, sample_age_s, warn_on, high_on):
    lvl = "-" if co2_f is None else quality_label(co2_f, warn_on, high_on)
    return (
        "🌿 Welcome to Air Monitor\n"
        "CO2: {} ppm ({})\n"
        "Temp: {} C | RH: {} %\n"
        "Sample age: {} s\n"
        "Commands: /status /info /health /diag /events /thresholds /settings /help"
    ).format(_fmt_int(co2_f), lvl, _fmt_1(temp_f), _fmt_1(rh_f), sample_age_s)


def _fmt_uptime_hhmmss(uptime_s):
    try:
        total = int(uptime_s)
    except Exception:
        return "-"
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return "{:02d}:{:02d}:{:02d}".format(h, m, s)


def render_health_card(uptime_s, health):
    power_txt = "BAD" if health.get("power_bad") else "GOOD"
    score = int(health.get("score", 0))
    mode_txt = health.get("mode", "NORMAL")
    try:
        err_rate = float(health.get("err_rate_per_min", 0.0))
    except Exception:
        err_rate = 0.0
    err_rate_txt = "{:.{p}f}".format(err_rate, p=HEALTH_ERR_RATE_DECIMALS)
    last_recover_age = health.get("last_recover_age_s", "-")
    last_power_bad_age = health.get("last_power_bad_age_s", "-")
    last_i2c_err_age = health.get("last_i2c_err_age_s", "-")
    time_sync_last_ok_age = health.get("time_sync_last_ok_age_s", "-")
    time_synced_txt = "YES" if health.get("time_synced") else "NO"
    bus_mode = health.get("bus_mode", "-")
    bus_freq = health.get("bus_freq_hz", "-")
    i2c_total = int(health.get("i2c_err_total", 0))
    recover_total = int(health.get("recover_total", 0))
    sensor_total = int(health.get("sensor_err_total", 0))
    oled_total = int(health.get("oled_init_err_total", 0))
    wifi_reconnect_total = int(health.get("wifi_reconnect_total", 0))
    tg_send_err_total = int(health.get("tg_send_err_total", 0))
    window_s = int(health.get("window_ms", PWR_DIAG_WINDOW_MS)) // 1000
    return (
        "🩺 System Health\n"
        "Mode: {}\n"
        "Power: {} (score {})\n"
        "Uptime: {}\n"
        "Err rate: {}/min ({}s)\n"
        "I2C errs: {} | Sensor: {} | OLED: {}\n"
        "Recovers: {} | Wi-Fi rc: {} | TG send err: {}\n"
        "Last recover: {} s | Last I2C err: {} s\n"
        "Last power bad: {} s | Time sync last OK: {} s\n"
        "Time sync: {}\n"
        "Bus: {} @ {} Hz"
    ).format(
        mode_txt,
        power_txt,
        score,
        _fmt_uptime_hhmmss(uptime_s),
        err_rate_txt,
        window_s,
        i2c_total,
        sensor_total,
        oled_total,
        recover_total,
        wifi_reconnect_total,
        tg_send_err_total,
        last_recover_age,
        last_i2c_err_age,
        last_power_bad_age,
        time_sync_last_ok_age,
        time_synced_txt,
        bus_mode,
        bus_freq,
    )


def render_events_card(events):
    lines = ["🧾 Recent Events"]
    if not events:
        lines.append("No events yet.")
        return "\n".join(lines)
    for ev in events:
        ts = ev.get("ts_ms", 0)
        code = ev.get("code", "-")
        lvl = ev.get("level", "-")
        msg = ev.get("msg_short", "-")
        ctx = ev.get("context", "")
        line = "[{}] {} {}".format(lvl, code, msg)
        if ctx:
            line += " ({})".format(ctx)
        lines.append(line)
        lines.append("t+{}ms".format(ts))
    return "\n".join(lines)


def render_diag_card(uptime_s, health):
    recents = health.get("recent_events", [])
    events_preview = "none"
    if recents:
        events_preview = "; ".join(
            ["{}:{}".format(ev.get("level", "-"), ev.get("code", "-")) for ev in recents[:DIAG_CMD_EVENTS_LIMIT]]
        )
    return (
        "📟 Ops Snapshot\n"
        "Uptime: {}\n"
        "Mode: {} | Power: {} (score {})\n"
        "Bus: {} @ {} Hz\n"
        "Recovers: {} | I2C errs: {}\n"
        "Last recover: {} s | Last I2C err: {} s\n"
        "Recent: {}"
    ).format(
        _fmt_uptime_hhmmss(uptime_s),
        health.get("mode", "NORMAL"),
        "BAD" if health.get("power_bad") else "GOOD",
        int(health.get("score", 0)),
        health.get("bus_mode", "-"),
        health.get("bus_freq_hz", "-"),
        int(health.get("recover_total", 0)),
        int(health.get("i2c_err_total", 0)),
        health.get("last_recover_age_s", "-"),
        health.get("last_i2c_err_age_s", "-"),
        events_preview,
    )


def render_alert_high(co2_f, temp_f, rh_f, reminder=False):
    if reminder:
        title = "⚠️ Reminder: ventilate now."
    else:
        title = "⚠️ Ventilate now."
    return (
        "{}\n"
        "CO2: {} ppm\n"
        "Temp: {} C\n"
        "RH: {} %\n"
        "Open /menu for controls."
    ).format(title, _fmt_int(co2_f), _fmt_1(temp_f), _fmt_1(rh_f))


def render_alert_recovery(co2_f, temp_f, rh_f):
    return (
        "✅ Air is back to normal.\n"
        "CO2: {} ppm\n"
        "Temp: {} C\n"
        "RH: {} %"
    ).format(_fmt_int(co2_f), _fmt_1(temp_f), _fmt_1(rh_f))


def render_menu_section(
    section,
    co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
    sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
    local_time_txt, time_synced, time_sync_error, quiet_now
):
    if section == "home":
        return render_menu_home_card(
            co2_f, temp_f, rh_f, sample_age_s, state["warn_on"], state["high_on"]
        ), menu_keyboard_home()
    if section == "status":
        return render_status_card(
            co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok,
            state["warn_on"], state["high_on"]
        ), menu_keyboard_status()
    if section == "details":
        return render_details_card(
            co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
            sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan,
            state["warn_on"], state["high_on"], local_time_txt, time_synced, time_sync_error, quiet_now
        ), menu_keyboard_details()
    if section == "controls":
        return render_controls_card(), menu_keyboard_controls()
    if section == "settings":
        return render_settings_card(
            state["warn_on"], state["high_on"], state["remind_min"]
        ), settings_keyboard()
    if section == "thresholds":
        return render_thresholds_card(
            state["warn_on"], state["high_on"]
        ), thresholds_keyboard()
    return render_help_card(), menu_keyboard_help()
