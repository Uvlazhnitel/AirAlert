from machine import Pin, I2C
import time
import framebuf
import sh1106
import network
import socket
try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests as requests
except ImportError:
    requests = None

try:
    import secrets
except ImportError:
    secrets = None

# ===================== Hardware =====================
SCD4X_ADDR = 0x62
OLED_ADDR = 0x3C

SCD_SDA = 3
SCD_SCL = 9
OLED_SDA = 13
OLED_SCL = 12

W = 128
H = 64

# Lower OLED bus speed for better stability with shared 3V3 rail noise
SCD_I2C_FREQ = 50_000
OLED_I2C_FREQ = 100_000

# Accuracy/config per SCD41 datasheet
ASC_ENABLED = False
ASC_TARGET_PPM = None          # e.g. 420, None = keep default target
AMBIENT_PRESSURE_PA = None     # e.g. 101300, overrides altitude if set
ALTITUDE_M = 0                 # used when AMBIENT_PRESSURE_PA is None
TEMP_OFFSET_C = None           # internal SCD temp offset (0..20C), None = keep default
PERSIST_SETTINGS = False       # write settings to sensor EEPROM (use sparingly)

# Display correction only (does not affect sensor math)
TEMP_CORR_C = 1.9

# Display refresh rate
UI_REFRESH_MS = 1200
SCREEN_SWITCH_MS = 5000
READY_LOG_EVERY_MS = 3000
READY_POLL_MS = 500
OLED_INIT_RETRY_MS = 2000
SCD_RESTART_MS = 90000
UI_STALE_SEC = 15
SCD_FAST_RECOVERY_MS = 25000
I2C_SCAN_LOG_EVERY_MS = 10000
UI_MODE = "infographic"
UI_SHOW_TREND = True
UI_CO2_BAR_MIN = 400
UI_CO2_BAR_MAX = 2400
UI_TREND_DEADBAND = 20
UI_BLINK_HIGH = True
UI_TEMP_TREND_DEADBAND = 0.2
UI_RH_TREND_DEADBAND = 1.0

# EMA for smoother display while keeping raw logs
EMA_CO2_ALPHA = 0.35
EMA_T_ALPHA = 0.20
EMA_RH_ALPHA = 0.20

# Network / Telegram
WIFI_SSID = getattr(secrets, "WIFI_SSID", "") if secrets else ""
WIFI_PASS = getattr(secrets, "WIFI_PASS", "") if secrets else ""

TG_ENABLE = True
TG_TOKEN = getattr(secrets, "TG_TOKEN", "").strip() if secrets else ""
TG_CHAT_ID = getattr(secrets, "TG_CHAT_ID", 0) if secrets else 0
TG_ALLOWED_USER_ID = getattr(secrets, "TG_ALLOWED_USER_ID", TG_CHAT_ID) if secrets else TG_CHAT_ID
TG_MIN_GAP_MS = 1500
TG_REMIND_MS = 20 * 60 * 1000
TG_TIMEOUT_S = 5
WIFI_RECONNECT_MS = 10000
TG_CMDS_ENABLE = True
TG_CMD_POLL_MS = 8000
TG_GETUPDATES_LIMIT = 10
TG_INLINE_KEYBOARD_ENABLE = False

# Night quiet mode (local board time)
QUIET_ENABLE = True
QUIET_START_H = 0
QUIET_END_H = 10

LVL_GOOD = 0
LVL_OK = 1
LVL_HIGH = 2

# Runtime thresholds (persisted in state.json)
DEFAULT_WARN_ON = 800
DEFAULT_HIGH_ON = 1500
DEFAULT_REMIND_MIN = 20

WARN_MIN = 600
WARN_MAX = 1400
HIGH_MIN = 1000
HIGH_MAX = 3000
HIGH_OVER_WARN_MIN_GAP = 200
REMIND_MIN_MIN = 5
REMIND_MIN_MAX = 120

STATE_FILE = "state.json"
STATE_TMP = "state.tmp"


# ===================== Sensirion CRC =====================
def crc8(data):
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) & 0xFF) ^ 0x31
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_cmd(cmd, args_words=None):
    out = bytes([(cmd >> 8) & 0xFF, cmd & 0xFF])
    if args_words:
        for w in args_words:
            d = bytes([(w >> 8) & 0xFF, w & 0xFF])
            out += d + bytes([crc8(d)])
    return out


def parse_words_with_crc(buf):
    if len(buf) % 3 != 0:
        raise ValueError("Bad length")
    words = []
    for i in range(0, len(buf), 3):
        d = buf[i:i + 2]
        c = buf[i + 2]
        if crc8(d) != c:
            raise ValueError("CRC mismatch")
        words.append((d[0] << 8) | d[1])
    return words


# ===================== SCD41 =====================
class SCD41:
    def __init__(self, i2c, addr=SCD4X_ADDR):
        self.i2c = i2c
        self.addr = addr

    def _write_cmd(self, cmd, args_words=None):
        self.i2c.writeto(self.addr, build_cmd(cmd, args_words))

    def _read_words(self, nwords):
        return parse_words_with_crc(self.i2c.readfrom(self.addr, nwords * 3))

    def stop_periodic_measurement(self):
        self._write_cmd(0x3F86)
        time.sleep_ms(500)

    def wake_up(self):
        self._write_cmd(0x36F6)
        time.sleep_ms(20)

    def reinit(self):
        self._write_cmd(0x3646)
        time.sleep_ms(30)

    def start_periodic_measurement(self):
        self._write_cmd(0x21B1)
        time.sleep_ms(20)

    def get_data_ready_raw(self):
        self._write_cmd(0xE4B8)
        time.sleep_ms(1)
        return self._read_words(1)[0]

    def get_data_ready_status(self):
        return (self.get_data_ready_raw() & 0x07FF) != 0

    def read_measurement(self):
        self._write_cmd(0xEC05)
        time.sleep_ms(1)
        co2_raw, t_raw, rh_raw = self._read_words(3)
        temp = -45.0 + 175.0 * (t_raw / 65535.0)
        rh = 100.0 * (rh_raw / 65535.0)
        return co2_raw, temp, rh

    def set_asc_enabled(self, enabled):
        self._write_cmd(0x2416, [1 if enabled else 0])
        time.sleep_ms(1)

    def set_asc_target_ppm(self, ppm):
        ppm = int(max(400, min(2000, ppm)))
        self._write_cmd(0x243A, [ppm])
        time.sleep_ms(1)

    def set_sensor_altitude(self, altitude_m):
        altitude_m = int(max(0, min(3000, altitude_m)))
        self._write_cmd(0x2427, [altitude_m])
        time.sleep_ms(1)

    def set_ambient_pressure_pa(self, pressure_pa):
        hpa = int(round(float(pressure_pa) / 100.0))
        hpa = int(max(700, min(1200, hpa)))
        self._write_cmd(0xE000, [hpa])
        time.sleep_ms(1)

    def set_temperature_offset(self, offset_c):
        offset_c = float(offset_c)
        if offset_c < 0:
            offset_c = 0.0
        if offset_c > 20:
            offset_c = 20.0
        word = int(round(offset_c * 65535.0 / 175.0))
        self._write_cmd(0x241D, [word])
        time.sleep_ms(1)

    def persist_settings(self):
        self._write_cmd(0x3615)
        time.sleep_ms(800)


def ema(prev, x, alpha):
    return x if prev is None else (prev + alpha * (x - prev))


def url_escape(s):
    out = []
    for b in s.encode("utf-8"):
        if (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in b"-_.~":
            out.append(chr(b))
        elif b == 32:
            out.append("%20")
        else:
            out.append("%{:02X}".format(b))
    return "".join(out)


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


def level_from_co2(co2, warn_on, high_on):
    if co2 < warn_on:
        return LVL_GOOD
    if co2 <= high_on:
        return LVL_OK
    return LVL_HIGH


def is_quiet_now():
    if not QUIET_ENABLE:
        return False
    hour = time.localtime()[3]
    if QUIET_START_H < QUIET_END_H:
        return QUIET_START_H <= hour < QUIET_END_H
    return (hour >= QUIET_START_H) or (hour < QUIET_END_H)


def wifi_connect(timeout_ms=15000, attempts=3):
    wlan = network.WLAN(network.STA_IF)
    if not WIFI_SSID:
        try:
            wlan.active(True)
        except Exception:
            pass
        return wlan

    for n in range(attempts):
        try:
            # Hard reset STA state to recover from "Wifi Internal State Error"
            try:
                wlan.disconnect()
            except Exception:
                pass
            wlan.active(False)
            time.sleep_ms(250)
            wlan.active(True)
            time.sleep_ms(250)
        except Exception as e:
            print("WiFi iface reset error:", e)

        if wlan.isconnected():
            return wlan

        try:
            wlan.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            print("WiFi connect error:", e, "attempt", n + 1, "/", attempts)
            time.sleep_ms(700)
            continue

        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                break
            time.sleep_ms(200)

        if wlan.isconnected():
            return wlan

        print("WiFi timeout attempt", n + 1, "/", attempts)
        time.sleep_ms(700)

    return wlan


_last_tg_send = 0
_last_update_id = 0


def safe_close(r):
    try:
        if r:
            r.close()
    except Exception:
        pass


def tg_send(text, reply_markup=None):
    global _last_tg_send

    if (not TG_ENABLE) or (not TG_TOKEN) or (TG_CHAT_ID == 0):
        return False
    if requests is None:
        return False

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_tg_send) < TG_MIN_GAP_MS:
        return False

    r = None
    try:
        if not TG_INLINE_KEYBOARD_ENABLE:
            reply_markup = None
        payload = {"chat_id": TG_CHAT_ID, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload)
        url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
        r = requests.post(url, data=data, headers={"Content-Type": "application/json"})
        code = getattr(r, "status_code", None)
        if code is not None and code != 200:
            print("TG HTTP:", code)
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


def render_details_card(co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, warn_on, high_on):
    lvl = "-" if co2_f is None else quality_label(co2_f, warn_on, high_on)
    quiet_now = "YES" if is_quiet_now() else "NO"
    return (
        "🔎 System Details\n"
        "Wi-Fi: {} | Sensor: {}\n"
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
        quiet_now,
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
        "Commands: /menu /status /info /thresholds /settings /help\n"
        "/thresholds -> WARN/HIGH\n"
        "/settings -> WARN/HIGH/REM"
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
        "Commands: /status /info /thresholds /settings /help"
    ).format(_fmt_int(co2_f), lvl, _fmt_1(temp_f), _fmt_1(rh_f), sample_age_s)


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


def render_menu_section(section, co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state):
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
            state["warn_on"], state["high_on"]
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
        return tg_send(text)
    if message_id is not None:
        if tg_edit_message(chat_id, message_id, text, kb):
            return True
    return tg_send(text, reply_markup=kb)


def settings_text(warn_on, high_on, remind_min):
    return render_settings_card(warn_on, high_on, remind_min)


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


def status_text(co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, warn_on, high_on, remind_min):
    _ = remind_min
    return render_status_card(co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, warn_on, high_on)


def info_text(co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, warn_on, high_on):
    return render_details_card(
        co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f, sample_age_s,
        sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, warn_on, high_on
    )


def tg_poll_commands(
    co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
    sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
):
    global _last_update_id

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
                ok, human = apply_cfg_callback(state, cb_data)
                tg_answer_callback(cb_id, human if human else "Updated")
                if cb_data.startswith("cfg:preset:"):
                    section = "controls"
                else:
                    section = "settings"
            elif cb_data.startswith("thr:"):
                ok, human = apply_cfg_callback(state, cb_data)
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
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            _tg_send_or_edit(chat_id, message_id, txt, kb)
            continue

        msg = upd.get("message")
        if not msg:
            continue

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

        if cmd == "menu":
            txt, kb = render_menu_section(
                "home",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)
        elif cmd == "status":
            txt, kb = render_menu_section(
                "status",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)
        elif cmd == "info":
            txt, kb = render_menu_section(
                "details",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)
        elif cmd == "settings":
            txt, kb = render_menu_section(
                "settings",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)
        elif cmd == "thresholds":
            txt, kb = render_menu_section(
                "thresholds",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)
        elif cmd == "help":
            txt, kb = render_menu_section(
                "help",
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan, state
            )
            tg_send(txt, reply_markup=kb)

    return state["warn_on"], state["high_on"], state["remind_min"]


# ===================== UI helpers =====================
def text_scaled(oled, s, x, y, scale=2):
    bw = len(s) * 8
    bh = 8
    buf = bytearray((bw * bh + 7) // 8)
    fb = framebuf.FrameBuffer(buf, bw, bh, framebuf.MONO_HLSB)
    fb.fill(0)
    fb.text(s, 0, 0, 1)

    for yy in range(bh):
        for xx in range(bw):
            if fb.pixel(xx, yy):
                bx = x + xx * scale
                by = y + yy * scale
                for dy in range(scale):
                    py = by + dy
                    if py < 0 or py >= H:
                        continue
                    for dx in range(scale):
                        px = bx + dx
                        if 0 <= px < W:
                            oled.pixel(px, py, 1)


def centered_x(text, scale):
    px = len(text) * 8 * scale
    x = (W - px) // 2
    return 0 if x < 0 else x


def clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def quality_label(co2, warn_on, high_on):
    if co2 < warn_on:
        return "GOOD"
    if co2 <= high_on:
        return "OK"
    return "HIGH"


def trend_from_delta(delta, deadband):
    if delta > deadband:
        return 1
    if delta < -deadband:
        return -1
    return 0


def draw_status_chip(oled, x, y, text, invert=False):
    tw = len(text) * 8 + 4
    th = 10
    if invert:
        oled.fill_rect(x, y, tw, th, 1)
        oled.text(text, x + 2, y + 1, 0)
    else:
        oled.fill_rect(x, y, tw, th, 0)
        oled.rect(x, y, tw, th, 1)
        oled.text(text, x + 2, y + 1, 1)
    return tw


def draw_bar(oled, x, y, w, h, value, vmin, vmax):
    oled.rect(x, y, w, h, 1)
    if vmax <= vmin:
        return
    ratio = (clamp(value, vmin, vmax) - vmin) / float(vmax - vmin)
    fill = int((w - 2) * ratio)
    if fill > 0:
        oled.fill_rect(x + 1, y + 1, fill, h - 2, 1)


def draw_trend_arrow(oled, x, y, trend):
    if trend > 0:
        oled.text("^", x, y, 1)
    elif trend < 0:
        oled.text("v", x, y, 1)
    else:
        oled.text("-", x, y, 1)


def draw_header(oled, title, age_s, status=None, status_blink=False):
    oled.fill_rect(0, 0, 128, 10, 1)
    oled.text(title, 2, 1, 0)
    if isinstance(age_s, int):
        age_txt = "{}s".format(age_s)
    else:
        age_txt = str(age_s)
    oled.text(age_txt, 104, 1, 0)
    if status:
        draw_status_chip(oled, 44, 0, status, invert=bool(status_blink))


def draw_co2_screen_v2(oled, co2, age_s, warn_on, high_on, trend_dir=0, high_blink_phase=False):
    oled.fill(0)

    label = quality_label(co2, warn_on, high_on)
    blink = UI_BLINK_HIGH and label == "HIGH" and high_blink_phase
    draw_header(oled, "CO2", age_s, status=label, status_blink=blink)

    co2s = str(int(round(co2)))
    sc = 3 if len(co2s) <= 3 else 2
    x = centered_x(co2s, sc)
    y = 14 if sc == 3 else 18
    text_scaled(oled, co2s, x, y, sc)
    oled.text("ppm", 98, 35, 1)

    if UI_SHOW_TREND:
        draw_trend_arrow(oled, 118, 16, trend_dir)

    draw_bar(oled, 4, 56, 120, 8, co2, UI_CO2_BAR_MIN, UI_CO2_BAR_MAX)

    oled.show()


def draw_temp_screen(oled, temp, age_s, trend_dir=0):
    oled.fill(0)
    draw_header(oled, "TEMP", age_s)

    sign = "-" if temp < 0 else ""
    t = abs(float(temp))
    ti = int(t)
    td = int(round((t - ti) * 10))
    if td == 10:
        ti += 1
        td = 0

    big = sign + str(ti)
    dec = ".{}".format(td)

    sb = 4 if len(big) <= 2 else 3
    sd = 2
    bw = len(big) * 8 * sb
    dw = len(dec) * 8 * sd
    x = (W - (bw + dw + 2)) // 2
    yb = 18
    yd = yb + (sb * 8 - sd * 8)

    text_scaled(oled, big, x, yb, sb)
    text_scaled(oled, dec, x + bw + 2, yd, sd)
    oled.text("C", 118, 40, 1)
    if UI_SHOW_TREND:
        draw_trend_arrow(oled, 118, 16, trend_dir)
    draw_bar(oled, 4, 56, 120, 8, temp, -10, 40)
    oled.show()


def draw_hum_screen(oled, rh, age_s, trend_dir=0):
    oled.fill(0)
    draw_header(oled, "HUM", age_s)

    hs = "{:.0f}".format(rh)
    sc = 5 if len(hs) <= 2 else 4
    x = centered_x(hs, sc)
    y = 16 if sc == 5 else 18
    text_scaled(oled, hs, x, y, sc)
    oled.text("%", 116, 40, 1)
    if UI_SHOW_TREND:
        draw_trend_arrow(oled, 118, 16, trend_dir)
    draw_bar(oled, 4, 56, 120, 8, rh, 0, 100)
    oled.show()


def draw_stale(oled, co2, temp, rh, age_s):
    oled.fill(0)
    draw_header(oled, "DATA", age_s, status="STALE")
    oled.text("No fresh sample", 10, 18, 1)
    oled.text("CO2 {:>4}".format(int(round(co2))), 10, 32, 1)
    oled.text("T {:>4.1f}C RH {:>2.0f}%".format(temp, rh), 10, 44, 1)
    oled.show()


def draw_screen(oled, screen_idx, co2, temp, rh, age_s, warn_on, high_on, trend_dir=0, temp_trend_dir=0, rh_trend_dir=0, high_blink_phase=False):
    if screen_idx == 0:
        draw_co2_screen_v2(oled, co2, age_s, warn_on, high_on, trend_dir, high_blink_phase)
    elif screen_idx == 1:
        draw_temp_screen(oled, temp, age_s, temp_trend_dir)
    else:
        draw_hum_screen(oled, rh, age_s, rh_trend_dir)


def draw_warmup(oled):
    oled.fill(0)
    draw_header(oled, "SENSOR", "-", status="WARMUP")
    oled.text("SCD41 warmup", 18, 24, 1)
    oled.text("Waiting first sample", 0, 38, 1)
    oled.show()


def draw_error(oled, line1, line2=""):
    oled.fill(0)
    draw_header(oled, "OLED", "-", status="ERROR")
    oled.text(line1[:21], 0, 24, 1)
    oled.text(line2[:21], 0, 36, 1)
    oled.show()


# ===================== Main =====================
def main():
    print("=== CO2 OLED Monitor ===")
    boot_ms = time.ticks_ms()

    state = apply_state_defaults(load_state())
    warn_on = int(state["warn_on"])
    high_on = int(state["high_on"])
    remind_min = int(state["remind_min"])
    remind_ms = remind_min * 60 * 1000
    print("Runtime settings: WARN", warn_on, "HIGH", high_on, "REM", remind_min, "min")

    try:
        socket.setdefaulttimeout(TG_TIMEOUT_S)
    except Exception:
        pass

    wlan = wifi_connect()
    print("WiFi connected:", wlan.isconnected())

    i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=SCD_I2C_FREQ)
    scd_scan = [hex(x) for x in i2c_scd.scan()]
    print("SCD I2C scan:", scd_scan)

    if SCD4X_ADDR not in i2c_scd.scan():
        print("SCD41 not found")
        return

    scd = SCD41(i2c_scd)
    oled = None
    oled_ok = False

    try:
        scd.stop_periodic_measurement()
    except Exception as e:
        print("stop_periodic_measurement:", e)
    try:
        scd.wake_up()
    except Exception as e:
        print("wake_up:", e)
    try:
        scd.reinit()
    except Exception as e:
        print("reinit:", e)

    # Apply accuracy settings in idle mode before start_periodic_measurement.
    try:
        scd.set_asc_enabled(ASC_ENABLED)
        print("ASC:", "ON" if ASC_ENABLED else "OFF")
    except Exception as e:
        print("ASC config error:", e)

    if ASC_TARGET_PPM is not None:
        try:
            scd.set_asc_target_ppm(ASC_TARGET_PPM)
            print("ASC target:", ASC_TARGET_PPM)
        except Exception as e:
            print("ASC target error:", e)

    try:
        if AMBIENT_PRESSURE_PA is not None:
            scd.set_ambient_pressure_pa(AMBIENT_PRESSURE_PA)
            print("Pressure Pa:", AMBIENT_PRESSURE_PA)
        else:
            scd.set_sensor_altitude(ALTITUDE_M)
            print("Altitude m:", ALTITUDE_M)
    except Exception as e:
        print("Pressure/altitude config error:", e)

    if TEMP_OFFSET_C is not None:
        try:
            scd.set_temperature_offset(TEMP_OFFSET_C)
            print("Temp offset C:", TEMP_OFFSET_C)
        except Exception as e:
            print("Temp offset config error:", e)

    if PERSIST_SETTINGS:
        try:
            scd.persist_settings()
            print("Sensor settings persisted")
        except Exception as e:
            print("Persist settings error:", e)

    try:
        scd.start_periodic_measurement()
    except Exception as e:
        print("SCD start failed:", e)
        return

    last_ui_draw = time.ticks_add(time.ticks_ms(), -UI_REFRESH_MS)
    last_screen_switch = time.ticks_ms()
    last_ready_log = time.ticks_add(time.ticks_ms(), -READY_LOG_EVERY_MS)
    last_ready_poll = time.ticks_add(time.ticks_ms(), -READY_POLL_MS)
    last_wifi = time.ticks_ms()
    last_cmd = time.ticks_add(time.ticks_ms(), -TG_CMD_POLL_MS)
    last_sample_ms = None
    co2 = None
    temp = None
    rh = None
    co2_f = None
    temp_f = None
    rh_f = None
    warm_start = time.ticks_ms()
    last_restart = warm_start
    fast_recovery_done = False
    ready_raw = 0
    screen_idx = 0
    prev_lvl = LVL_GOOD
    last_remind = time.ticks_add(time.ticks_ms(), -remind_ms)
    oled_scan = "-"
    last_oled_init_try = time.ticks_add(time.ticks_ms(), -OLED_INIT_RETRY_MS)
    last_i2c_scan_log = time.ticks_add(time.ticks_ms(), -I2C_SCAN_LOG_EVERY_MS)
    last_co2_for_trend = None
    last_temp_for_trend = None
    last_rh_for_trend = None
    trend_dir = 0
    temp_trend_dir = 0
    rh_trend_dir = 0
    high_blink_phase = False
    stale_ui = False
    warmup_drawn = False

    while True:
        now = time.ticks_ms()

        if (not oled_ok) and (time.ticks_diff(now, last_oled_init_try) >= OLED_INIT_RETRY_MS):
            last_oled_init_try = now
            try:
                i2c_oled = I2C(1, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=OLED_I2C_FREQ)
                scan = i2c_oled.scan()
                oled_scan_list = [hex(x) for x in scan]
                oled_scan = ",".join(oled_scan_list)
                log_scan_now = time.ticks_diff(now, last_i2c_scan_log) >= I2C_SCAN_LOG_EVERY_MS
                if log_scan_now:
                    last_i2c_scan_log = now
                    print("OLED I2C scan:", oled_scan_list)
                if OLED_ADDR in scan:
                    oled = sh1106.SH1106_I2C(W, H, i2c_oled, addr=OLED_ADDR)
                    oled.sleep(False)
                    oled_ok = True
                    warmup_drawn = False
                    print("OLED init: OK")
                    try:
                        draw_warmup(oled)
                        warmup_drawn = True
                    except Exception as e:
                        print("OLED warmup draw error:", e)
                        oled_ok = False
                else:
                    if log_scan_now:
                        print("OLED not found at 0x3C")
            except Exception as e:
                print("OLED init error:", e)

        if time.ticks_diff(now, last_ready_poll) >= READY_POLL_MS:
            last_ready_poll = now
            try:
                ready_raw = scd.get_data_ready_raw()
                ready = (ready_raw & 0x07FF) != 0
                if ready:
                    co2, temp, rh = scd.read_measurement()
                    if co2 < 350 or co2 > 10000:
                        time.sleep_ms(20)
                        continue
                    temp = temp + TEMP_CORR_C
                    co2_f = ema(co2_f, co2, EMA_CO2_ALPHA)
                    temp_f = ema(temp_f, temp, EMA_T_ALPHA)
                    rh_f = ema(rh_f, rh, EMA_RH_ALPHA)

                    if last_co2_for_trend is not None:
                        trend_dir = trend_from_delta(co2_f - last_co2_for_trend, UI_TREND_DEADBAND)
                    else:
                        trend_dir = 0
                    if last_temp_for_trend is not None:
                        temp_trend_dir = trend_from_delta(temp_f - last_temp_for_trend, UI_TEMP_TREND_DEADBAND)
                    else:
                        temp_trend_dir = 0
                    if last_rh_for_trend is not None:
                        rh_trend_dir = trend_from_delta(rh_f - last_rh_for_trend, UI_RH_TREND_DEADBAND)
                    else:
                        rh_trend_dir = 0
                    last_co2_for_trend = co2_f
                    last_temp_for_trend = temp_f
                    last_rh_for_trend = rh_f
                    last_sample_ms = now
                    print("RAW CO2:{} T:{:.2f} RH:{:.2f} | FILT CO2:{} T:{:.2f} RH:{:.2f}".format(
                        co2, temp, rh, int(round(co2_f)), temp_f, rh_f
                    ))

                    lvl = level_from_co2(co2_f, warn_on, high_on)
                    if wlan.isconnected() and TG_ENABLE:
                        if (prev_lvl != LVL_HIGH) and (lvl == LVL_HIGH):
                            if is_quiet_now():
                                print("TG quiet: HIGH muted")
                            else:
                                if tg_send_alert(
                                    render_alert_high(co2_f, temp_f, rh_f, reminder=False)
                                ):
                                    print("TG alert: HIGH sent")
                                    last_remind = now
                        elif (lvl == LVL_HIGH) and (time.ticks_diff(now, last_remind) > remind_ms):
                            if not is_quiet_now():
                                if tg_send_alert(
                                    render_alert_high(co2_f, temp_f, rh_f, reminder=True)
                                ):
                                    print("TG alert: HIGH reminder sent")
                                    last_remind = now
                        elif (prev_lvl == LVL_HIGH) and (lvl == LVL_GOOD):
                            if tg_send_alert(
                                render_alert_recovery(co2_f, temp_f, rh_f)
                            ):
                                print("TG alert: GOOD sent")
                    prev_lvl = lvl
            except Exception as e:
                print("Sensor read error:", e)

        if time.ticks_diff(now, last_ready_log) >= READY_LOG_EVERY_MS:
            last_ready_log = now
            age = "-" if last_sample_ms is None else str(time.ticks_diff(now, last_sample_ms) // 1000)
            print("ready_raw:", ready_raw, "sample_age_s:", age)

        if time.ticks_diff(now, last_wifi) > WIFI_RECONNECT_MS:
            last_wifi = now
            if not wlan.isconnected():
                wlan = wifi_connect()
                if wlan.isconnected():
                    print("WiFi reconnected")

        if wlan.isconnected() and TG_CMDS_ENABLE and time.ticks_diff(now, last_cmd) > TG_CMD_POLL_MS:
            last_cmd = now
            age_s = "-" if last_sample_ms is None else str(time.ticks_diff(now, last_sample_ms) // 1000)
            uptime_s = time.ticks_diff(now, boot_ms) // 1000
            sensor_ok = bool(last_sample_ms is not None and time.ticks_diff(now, last_sample_ms) <= SCD_RESTART_MS)
            warn_on, high_on, remind_min = tg_poll_commands(
                co2, temp, rh, co2_f, temp_f, rh_f,
                age_s, sensor_ok, wlan.isconnected(), uptime_s, remind_ms, ",".join(scd_scan), oled_scan, state
            )
            remind_ms = remind_min * 60 * 1000

        stale_sensor = False
        if last_sample_ms is not None and time.ticks_diff(now, last_sample_ms) > SCD_RESTART_MS:
            stale_sensor = True

        if (not fast_recovery_done) and (last_sample_ms is None) and time.ticks_diff(now, warm_start) >= SCD_FAST_RECOVERY_MS:
            print("Fast SCD recovery: no first sample in", SCD_FAST_RECOVERY_MS // 1000, "s")
            try:
                scd.stop_periodic_measurement()
            except Exception as e:
                print("fast stop error:", e)
            try:
                scd.wake_up()
            except Exception as e:
                print("fast wake error:", e)
            try:
                scd.reinit()
            except Exception as e:
                print("fast reinit error:", e)
            try:
                scd.start_periodic_measurement()
            except Exception as e:
                print("fast start error:", e)
            fast_recovery_done = True
            last_restart = now

        if (co2 is None) or (last_sample_ms is None) or stale_sensor:
            if time.ticks_diff(now, last_restart) >= SCD_RESTART_MS:
                print("Restarting SCD41 measurement (no data/stale)")
                try:
                    scd.stop_periodic_measurement()
                except Exception as e:
                    print("stop error:", e)
                try:
                    scd.wake_up()
                except Exception as e:
                    print("wake error:", e)
                try:
                    scd.reinit()
                except Exception as e:
                    print("reinit error:", e)
                try:
                    scd.start_periodic_measurement()
                except Exception as e:
                    print("start error:", e)
                last_restart = now

        # Keep OLED independent from first sample; show warmup until data arrives.
        if (co2_f is None) or (last_sample_ms is None):
            if oled_ok and (not warmup_drawn):
                try:
                    draw_warmup(oled)
                    warmup_drawn = True
                except Exception as e:
                    print("OLED warmup draw error:", e)
                    oled_ok = False
            time.sleep_ms(120)
            continue

        warmup_drawn = False
        stale_ui = time.ticks_diff(now, last_sample_ms) > (UI_STALE_SEC * 1000)
        high_blink_phase = ((now // 1000) & 1) == 1

        if oled_ok and time.ticks_diff(now, last_ui_draw) >= UI_REFRESH_MS:
            if (not stale_ui) and time.ticks_diff(now, last_screen_switch) >= SCREEN_SWITCH_MS:
                last_screen_switch = now
                screen_idx = (screen_idx + 1) % 3
            last_ui_draw = now
            age = time.ticks_diff(now, last_sample_ms) // 1000
            try:
                if stale_ui:
                    draw_stale(oled, co2_f, temp_f, rh_f, age)
                elif UI_MODE == "infographic":
                    draw_screen(
                        oled, screen_idx, co2_f, temp_f, rh_f, age, warn_on, high_on,
                        trend_dir=trend_dir,
                        temp_trend_dir=temp_trend_dir,
                        rh_trend_dir=rh_trend_dir,
                        high_blink_phase=high_blink_phase
                    )
                else:
                    draw_screen(oled, screen_idx, co2_f, temp_f, rh_f, age, warn_on, high_on)
            except Exception as e:
                print("OLED draw error:", e)
                oled_ok = False

        time.sleep_ms(50)


main()
