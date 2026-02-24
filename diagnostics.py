import time


def diag_init(now_ms):
    return {
        "diag_boot_ms": now_ms,
        "diag_i2c_err_total": 0,
        "diag_sensor_err_total": 0,
        "diag_oled_init_err_total": 0,
        "diag_recover_total": 0,
        "diag_last_recover_ms": None,
        "diag_last_i2c_err_ms": None,
        "diag_last_power_bad_ms": None,
        "events": [],
        "power_bad": False,
        "window_score": 0,
        "window_err_rate_per_min": 0.0,
    }


def diag_add_event(state, now_ms, kind, weight):
    state["events"].append((now_ms, kind, int(weight)))


def diag_prune(state, now_ms, window_ms):
    events = state.get("events", [])
    while events and time.ticks_diff(now_ms, events[0][0]) > window_ms:
        events.pop(0)


def diag_compute(state, now_ms, cfg):
    window_ms = int(cfg.get("window_ms", 60000))
    bad_score = int(cfg.get("bad_score", 8))
    i2c_kind = cfg.get("i2c_kind", "i2c_err")

    diag_prune(state, now_ms, window_ms)
    events = state.get("events", [])

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
