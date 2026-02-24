import time

ERR_SENSOR_READ = "ERR_SENSOR_READ"
ERR_OLED_INIT = "ERR_OLED_INIT"
ERR_I2C_RECOVER = "ERR_I2C_RECOVER"
ERR_TG_SEND = "ERR_TG_SEND"
ERR_TIME_SYNC = "ERR_TIME_SYNC"
ERR_WIFI_RECONNECT = "ERR_WIFI_RECONNECT"
WARN_SCD_RESTART_LIMIT = "WARN_SCD_RESTART_LIMIT"


def _ticks_diff(now_ms, then_ms):
    try:
        return time.ticks_diff(now_ms, then_ms)
    except AttributeError:
        return int(now_ms) - int(then_ms)


def _prune_times(times, now_ms, window_ms):
    while times and _ticks_diff(now_ms, times[0]) > window_ms:
        times.pop(0)


def _prune_events(state, now_ms, window_ms):
    events = state.get("score_events", [])
    while events and _ticks_diff(now_ms, events[0][0]) > window_ms:
        events.pop(0)


def diag_init(now_ms, max_events=50):
    return {
        "diag_boot_ms": now_ms,
        "diag_i2c_err_total": 0,
        "diag_sensor_err_total": 0,
        "diag_oled_init_err_total": 0,
        "diag_recover_total": 0,
        "diag_wifi_reconnect_total": 0,
        "diag_time_sync_err_total": 0,
        "diag_tg_send_err_total": 0,
        "diag_last_recover_ms": None,
        "diag_last_i2c_err_ms": None,
        "diag_last_power_bad_ms": None,
        "diag_last_time_sync_ok_ms": now_ms,
        "score_events": [],
        "event_log": [],
        "err_counts": {},
        "max_events": int(max_events),
        "power_bad": False,
        "window_score": 0,
        "window_err_rate_per_min": 0.0,
        "runtime_degraded": False,
        "scd_restart_attempts": [],
    }


def diag_add_event(state, now_ms, kind, weight):
    state["score_events"].append((now_ms, kind, int(weight)))
    if len(state["score_events"]) > state["max_events"]:
        state["score_events"] = state["score_events"][-state["max_events"]:]


def diag_record_event(state, now_ms, code, msg_short, context="", level="ERR"):
    events = state["event_log"]
    events.append(
        {
            "ts_ms": now_ms,
            "code": code,
            "level": level,
            "msg_short": str(msg_short)[:80],
            "context": str(context)[:120],
        }
    )
    if len(events) > state["max_events"]:
        state["event_log"] = events[-state["max_events"]:]
    cnt = state["err_counts"].get(code, 0)
    state["err_counts"][code] = cnt + 1


def diag_get_recent_events(state, limit=10):
    events = state.get("event_log", [])
    if limit <= 0:
        return []
    return events[-limit:]


def diag_compute(state, now_ms, cfg):
    window_ms = int(cfg.get("window_ms", 60000))
    bad_score = int(cfg.get("bad_score", 8))
    i2c_kind = cfg.get("i2c_kind", "i2c_err")

    _prune_events(state, now_ms, window_ms)
    events = state.get("score_events", [])

    score = 0
    i2c_err_cnt = 0
    for _, kind, weight in events:
        score += int(weight)
        if kind == i2c_kind:
            i2c_err_cnt += 1

    per_min = 0.0
    if window_ms > 0:
        per_min = (i2c_err_cnt * 60000.0) / float(window_ms)

    power_bad = score >= bad_score
    state["window_score"] = score
    state["window_err_rate_per_min"] = per_min
    state["power_bad"] = power_bad

    return {"power_bad": power_bad, "score": score, "err_rate": per_min}


def diag_mark_recover(state, now_ms, weight=2):
    state["diag_recover_total"] += 1
    state["diag_last_recover_ms"] = now_ms
    diag_add_event(state, now_ms, "recover", weight)


def diag_mark_i2c_err(state, now_ms, source, weight=1):
    _ = source
    state["diag_i2c_err_total"] += 1
    state["diag_last_i2c_err_ms"] = now_ms
    diag_add_event(state, now_ms, "i2c_err", weight)


def diag_note_time_sync(state, now_ms, ok):
    if ok:
        state["diag_last_time_sync_ok_ms"] = now_ms
    else:
        state["diag_time_sync_err_total"] += 1


def diag_note_wifi_reconnect(state):
    state["diag_wifi_reconnect_total"] += 1


def diag_note_tg_send_error(state):
    state["diag_tg_send_err_total"] += 1


def diag_allow_scd_restart(state, now_ms, window_ms, max_attempts):
    attempts = state["scd_restart_attempts"]
    _prune_times(attempts, now_ms, window_ms)
    if len(attempts) >= max_attempts:
        state["runtime_degraded"] = True
        return False, len(attempts)
    attempts.append(now_ms)
    return True, len(attempts)
