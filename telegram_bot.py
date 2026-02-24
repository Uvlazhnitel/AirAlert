import time
try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests as requests
except ImportError:
    requests = None

from config import *
from state_store import save_state, validate_settings

_last_tg_send = 0
_last_update_id = 0
_last_tg_chat_id = 0


def safe_close(r):
    try:
        if r:
            r.close()
    except Exception:
        pass


def url_escape(s):
    out = []
    for b in str(s).encode("utf-8"):
        if (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in b"-_.~":
            out.append(chr(b))
        elif b == 32:
            out.append("%20")
        else:
            out.append("%{:02X}".format(b))
    return "".join(out)


def tg_send(text, reply_markup=None, chat_id=None):
    global _last_tg_send
    global _last_tg_chat_id

    target_chat_id = chat_id
    if target_chat_id is None:
        target_chat_id = _last_tg_chat_id if _last_tg_chat_id else TG_CHAT_ID
    try:
        target_chat_id_txt = str(int(target_chat_id))
    except Exception:
        target_chat_id_txt = str(target_chat_id)
    if (not TG_ENABLE) or (not TG_TOKEN) or (target_chat_id == 0):
        return False
    if requests is None:
        return False

    now = time.ticks_ms()
    gap = time.ticks_diff(now, _last_tg_send)
    if gap < TG_MIN_GAP_MS:
        time.sleep_ms(TG_MIN_GAP_MS - gap)
        now = time.ticks_ms()

    if text is None:
        text = ""
    try:
        text = str(text)
    except Exception:
        text = "<text error>"
    if len(text) > TG_TEXT_MAX_LEN:
        text = text[:TG_TEXT_MAX_LEN - 12] + "\n...truncated"

    r = None
    try:
        token_tail = "none"
        try:
            token_tail = TG_TOKEN[-6:] if TG_TOKEN else "none"
        except Exception:
            pass
        if not TG_INLINE_KEYBOARD_ENABLE:
            reply_markup = None
        form = "chat_id={}&text={}".format(
            url_escape(target_chat_id_txt),
            url_escape(text),
        )
        if reply_markup is not None:
            try:
                rm = json.dumps(reply_markup)
                form += "&reply_markup={}".format(url_escape(rm))
            except Exception:
                pass
        url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
        r = requests.post(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        code = getattr(r, "status_code", None)
        if code is not None and code != 200:
            desc = "-"
            body = "-"
            try:
                j = r.json()
                if j:
                    desc = j.get("description", "-")
            except Exception:
                pass
            try:
                body = r.text
            except Exception:
                pass
            if isinstance(body, str) and len(body) > 180:
                body = body[:180] + "..."
            print(
                "TG HTTP:", code,
                "chat_id:", target_chat_id_txt,
                "token_tail:", token_tail,
                "desc:", desc,
                "body:", body
            )
            return False
        resp = None
        try:
            resp = r.json()
        except Exception:
            resp = None
        if not (resp and resp.get("ok")):
            desc = "-"
            try:
                desc = resp.get("description", "-") if resp else "-"
            except Exception:
                pass
            print("TG send failed:", desc)
            return False
        _last_tg_send = now
        _last_tg_chat_id = target_chat_id
        return True
    except Exception as e:
        print("TG send error:", e)
        return False
    finally:
        safe_close(r)


def tg_send_alert(text):
    return tg_send(text)


def _tg_post(method, payload):
    if (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return None
    r = None
    try:
        url = "https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method)
        data = json.dumps(payload)
        r = requests.post(url, data=data, headers={"Content-Type": "application/json"})
        try:
            return r.json()
        except Exception:
            return None
    except Exception as e:
        print("TG post error:", e)
        return None
    finally:
        safe_close(r)


def tg_answer_callback(callback_id, text="Updated"):
    if not callback_id:
        return
    _tg_post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def tg_edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = _tg_post("editMessageText", payload)
    return bool(resp and resp.get("ok"))


def tg_get_updates(offset):
    if (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return None
    url = "https://api.telegram.org/bot{}/getUpdates?timeout=0&offset={}&limit={}".format(
        TG_TOKEN, offset, TG_GETUPDATES_LIMIT
    )
    r = None
    try:
        r = requests.get(url)
        try:
            return r.json()
        except Exception:
            return None
    except Exception as e:
        print("TG getUpdates error:", e)
        return None
    finally:
        safe_close(r)


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
        "Commands: /menu /status /info /health /thresholds /settings /help\n"
        "/health -> power diagnostics\n"
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
        "Commands: /status /info /health /thresholds /settings /help"
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
    try:
        err_rate = float(health.get("err_rate_per_min", 0.0))
    except Exception:
        err_rate = 0.0
    err_rate_txt = "{:.{p}f}".format(err_rate, p=HEALTH_ERR_RATE_DECIMALS)
    last_recover_age = health.get("last_recover_age_s", "-")
    if last_recover_age == "-":
        last_recover_txt = "never"
    else:
        last_recover_txt = "{} s ago".format(last_recover_age)
    time_synced_txt = "YES" if health.get("time_synced") else "NO"
    bus_mode = health.get("bus_mode", "-")
    bus_freq = health.get("bus_freq_hz", "-")
    i2c_total = int(health.get("i2c_err_total", 0))
    recover_total = int(health.get("recover_total", 0))
    window_s = int(health.get("window_ms", PWR_DIAG_WINDOW_MS)) // 1000
    return (
        "🩺 System Health\n"
        "Power: {} (score {})\n"
        "Uptime: {}\n"
        "Err rate: {}/min ({}s)\n"
        "I2C errs: {} | Recovers: {}\n"
        "Last recover: {}\n"
        "Time sync: {}\n"
        "Bus: {} @ {} Hz"
    ).format(
        power_txt,
        score,
        _fmt_uptime_hhmmss(uptime_s),
        err_rate_txt,
        window_s,
        i2c_total,
        recover_total,
        last_recover_txt,
        time_synced_txt,
        bus_mode,
        bus_freq,
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


def _tg_send_or_edit(chat_id, message_id, text, kb):
    if not TG_INLINE_KEYBOARD_ENABLE:
        return tg_send(text, chat_id=chat_id)
    if message_id is not None:
        if tg_edit_message(chat_id, message_id, text, kb):
            return True
    return tg_send(text, reply_markup=kb, chat_id=chat_id)


def apply_cfg_callback(state, cb_data):
    if not cb_data or (not cb_data.startswith("cfg:") and not cb_data.startswith("thr:")):
        return False, "Bad callback"
    is_thr = cb_data.startswith("thr:")

    warn_on = int(state["warn_on"])
    high_on = int(state["high_on"])
    remind_min = int(state["remind_min"])

    parts = cb_data.split(":")
    if len(parts) < 2:
        return False, "Bad callback"

    if parts[1] == "refresh":
        return True, "Refreshed"

    if parts[1] == "preset" and len(parts) >= 3:
        if parts[2] == "home":
            warn_on, high_on, remind_min = 800, 1500, 20
        else:
            return False, "Unknown preset"
    elif len(parts) >= 3:
        field = parts[1]
        try:
            delta = int(parts[2])
        except Exception:
            return False, "Bad delta"

        if field == "warn":
            warn_on += delta
        elif field == "high":
            high_on += delta
        elif field == "remind":
            if is_thr:
                return False, "Reminder is not available here"
            remind_min += delta
        else:
            return False, "Bad field"
    else:
        return False, "Bad callback"

    ok, msg = validate_settings(warn_on, high_on, remind_min)
    if not ok:
        return False, msg

    state["warn_on"] = int(warn_on)
    state["high_on"] = int(high_on)
    state["remind_min"] = int(remind_min)
    save_state(state)
    return True, "Updated"


def tg_poll_commands(
    co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
    sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
    time_synced, time_sync_error, local_time_txt, quiet_now, health_snapshot
):
    global _last_update_id
    global _last_tg_chat_id

    if (not TG_CMDS_ENABLE) or (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return state["warn_on"], state["high_on"], state["remind_min"]

    data = tg_get_updates(_last_update_id + 1)
    if not data or not data.get("ok"):
        return state["warn_on"], state["high_on"], state["remind_min"]

    for upd in data.get("result", []):
        uid = upd.get("update_id", 0)
        if uid > _last_update_id:
            _last_update_id = uid

        cq = upd.get("callback_query")
        if cq:
            if not TG_INLINE_KEYBOARD_ENABLE:
                tg_answer_callback(cq.get("id"), "Buttons disabled. Use commands.")
                continue
            from_id = (cq.get("from") or {}).get("id")
            if TG_ALLOWED_USER_ID and from_id != TG_ALLOWED_USER_ID:
                print("TG unauthorized user:", from_id)
                tg_answer_callback(cq.get("id"), "Not allowed")
                continue

            cb_id = cq.get("id")
            cb_data = cq.get("data", "")
            msg = cq.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id", TG_CHAT_ID)
            message_id = msg.get("message_id")
            section = "home"

            if cb_data.startswith("cfg:"):
                _, human = apply_cfg_callback(state, cb_data)
                tg_answer_callback(cb_id, human if human else "Updated")
                if cb_data.startswith("cfg:preset:"):
                    section = "controls"
                else:
                    section = "settings"
            elif cb_data.startswith("thr:"):
                _, human = apply_cfg_callback(state, cb_data)
                tg_answer_callback(cb_id, human if human else "Updated")
                section = "thresholds"
            elif cb_data.startswith("menu:"):
                parts = cb_data.split(":")
                action = parts[1] if len(parts) >= 2 else "home"
                if action == "refresh":
                    section = "home"
                    tg_answer_callback(cb_id, "Refreshed")
                elif action in ("home", "status", "details", "controls", "settings", "thresholds", "help"):
                    section = action
                    tg_answer_callback(cb_id, "Updated")
                else:
                    section = "home"
                    tg_answer_callback(cb_id, "Unknown action")
            else:
                tg_answer_callback(cb_id, "Unsupported action")
                section = "home"

            txt, kb = render_menu_section(
                section,
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            _tg_send_or_edit(chat_id, message_id, txt, kb)
            continue

        msg = upd.get("message")
        if not msg:
            continue

        chat_id = (msg.get("chat") or {}).get("id", TG_CHAT_ID)
        if chat_id:
            _last_tg_chat_id = chat_id
        from_id = (msg.get("from") or {}).get("id")
        if TG_ALLOWED_USER_ID and from_id != TG_ALLOWED_USER_ID:
            print("TG unauthorized user:", from_id)
            continue

        text = (msg.get("text") or "").strip()
        if not text:
            continue

        low = text.lower().strip()
        cmd = low.split()[0]
        if cmd.startswith("/"):
            cmd = cmd[1:]
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        print("TG cmd: /" + cmd)
        print("TG route from_id:", from_id, "chat_id:", chat_id)

        if cmd == "menu":
            txt, kb = render_menu_section(
                "home",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)
        elif cmd == "status":
            txt, kb = render_menu_section(
                "status",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)
        elif cmd == "info":
            txt, kb = render_menu_section(
                "details",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)
        elif cmd == "health":
            txt = render_health_card(uptime_s, health_snapshot)
            tg_send(txt, chat_id=chat_id)
        elif cmd == "settings":
            txt, kb = render_menu_section(
                "settings",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)
        elif cmd == "thresholds":
            txt, kb = render_menu_section(
                "thresholds",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)
        elif cmd == "help":
            txt, kb = render_menu_section(
                "help",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state,
                local_time_txt, time_synced, time_sync_error, quiet_now
            )
            if TG_INLINE_KEYBOARD_ENABLE:
                tg_send(txt, reply_markup=kb, chat_id=chat_id)
            else:
                tg_send(txt, chat_id=chat_id)

    return state["warn_on"], state["high_on"], state["remind_min"]
