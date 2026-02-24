try:
    import ujson as json
except ImportError:
    import json

from config import (
    STATE_FILE,
    STATE_TMP,
    DEFAULT_WARN_ON,
    DEFAULT_HIGH_ON,
    DEFAULT_REMIND_MIN,
    WARN_MIN,
    WARN_MAX,
    HIGH_MIN,
    HIGH_MAX,
    HIGH_OVER_WARN_MIN_GAP,
    REMIND_MIN_MIN,
    REMIND_MIN_MAX,
)


def clamp_int(v, lo, hi):
    try:
        x = int(v)
    except Exception:
        x = lo
    if x < lo:
        x = lo
    if x > hi:
        x = hi
    return x


def validate_settings(warn_on, high_on, remind_min):
    if warn_on < WARN_MIN or warn_on > WARN_MAX:
        return False, "WARN out of range"
    if high_on < HIGH_MIN or high_on > HIGH_MAX:
        return False, "HIGH out of range"
    if high_on < (warn_on + HIGH_OVER_WARN_MIN_GAP):
        return False, "HIGH must be >= WARN+200"
    if remind_min < REMIND_MIN_MIN or remind_min > REMIND_MIN_MAX:
        return False, "REM out of range"
    return True, "Updated"


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.loads(f.read())
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_TMP, "w") as f:
            f.write(json.dumps(state))
        try:
            import uos as os
        except ImportError:
            import os
        try:
            os.remove(STATE_FILE)
        except Exception:
            pass
        try:
            os.rename(STATE_TMP, STATE_FILE)
        except Exception:
            with open(STATE_TMP, "r") as src, open(STATE_FILE, "w") as dst:
                dst.write(src.read())
            try:
                os.remove(STATE_TMP)
            except Exception:
                pass
    except Exception as e:
        print("save_state error:", e)


def apply_state_defaults(state):
    warn_on = clamp_int(state.get("warn_on", DEFAULT_WARN_ON), WARN_MIN, WARN_MAX)
    high_on = clamp_int(state.get("high_on", DEFAULT_HIGH_ON), HIGH_MIN, HIGH_MAX)
    remind_min = clamp_int(state.get("remind_min", DEFAULT_REMIND_MIN), REMIND_MIN_MIN, REMIND_MIN_MAX)
    if high_on < (warn_on + HIGH_OVER_WARN_MIN_GAP):
        high_on = warn_on + HIGH_OVER_WARN_MIN_GAP
        if high_on > HIGH_MAX:
            warn_on = HIGH_MAX - HIGH_OVER_WARN_MIN_GAP
            high_on = HIGH_MAX
    state["warn_on"] = warn_on
    state["high_on"] = high_on
    state["remind_min"] = remind_min
    return state
