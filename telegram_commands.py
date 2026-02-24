from config import *
from state_store import save_state, validate_settings
from telegram_transport import (
    requests,
    tg_send,
    tg_edit_message,
    tg_answer_callback,
    tg_get_updates,
    consume_update_id,
    next_update_offset,
    remember_chat_id,
)
from telegram_render import (
    render_menu_section,
    render_health_card,
    render_diag_card,
    render_events_card,
)


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
    if (not TG_CMDS_ENABLE) or (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return state["warn_on"], state["high_on"], state["remind_min"]

    data = tg_get_updates(next_update_offset())
    if not data or not data.get("ok"):
        return state["warn_on"], state["high_on"], state["remind_min"]

    for upd in data.get("result", []):
        uid = upd.get("update_id", 0)
        consume_update_id(uid)

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
        remember_chat_id(chat_id)
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
            tg_send(render_health_card(uptime_s, health_snapshot), chat_id=chat_id)
        elif cmd == "diag":
            tg_send(render_diag_card(uptime_s, health_snapshot), chat_id=chat_id)
        elif cmd == "events":
            ev = health_snapshot.get("recent_events_all", health_snapshot.get("recent_events", []))
            tg_send(render_events_card(ev[-EVENTS_CMD_LIMIT:]), chat_id=chat_id)
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
