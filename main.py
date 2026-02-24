from machine import Pin, I2C
import time
import framebuf
import sh1106
import network
import socket

try:
    import ntptime
except ImportError:
    ntptime = None

from config import *
from state_store import apply_state_defaults, load_state
from telegram_bot import (
    tg_send_alert,
    render_alert_high,
    render_alert_recovery,
    tg_poll_commands,
)


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


def level_from_co2(co2, warn_on, high_on):
    if co2 < warn_on:
        return LVL_GOOD
    if co2 <= high_on:
        return LVL_OK
    return LVL_HIGH


def _is_leap_year(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)


def _days_in_month(y, m):
    if m == 2:
        return 29 if _is_leap_year(y) else 28
    if m in (1, 3, 5, 7, 8, 10, 12):
        return 31
    return 30


def _format_local_time(lt):
    if not lt:
        return "-"
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(
        lt[0], lt[1], lt[2], lt[3], lt[4]
    )


def is_dst_eu_utc(ts):
    if DST_REGION != "EU":
        return False
    try:
        utc = time.gmtime(ts)
    except Exception:
        utc = time.localtime(ts)

    y, m, d, h = utc[0], utc[1], utc[2], utc[3]
    wday = utc[6]

    if m < 3 or m > 10:
        return False
    if 3 < m < 10:
        return True

    dim = _days_in_month(y, m)
    weekday_last = (wday + (dim - d)) % 7
    last_sunday = dim - ((weekday_last - 6) % 7)

    if m == 3:
        return (d > last_sunday) or (d == last_sunday and h >= 1)
    return (d < last_sunday) or (d == last_sunday and h < 1)


def localtime_now():
    global _time_synced
    try:
        utc_ts = int(time.time())
    except Exception:
        return None, False

    # MicroPython ports may use different epochs; validate by calendar year.
    try:
        base = time.gmtime(utc_ts)
    except Exception:
        base = time.localtime(utc_ts)
    if not base or base[0] < 2020:
        return None, False

    dst_sec = 0
    if DST_ENABLE and is_dst_eu_utc(utc_ts):
        dst_sec = 3600

    try:
        local_ts = utc_ts + TZ_OFFSET_MIN * 60 + dst_sec
        return time.localtime(local_ts), _time_synced
    except Exception:
        return None, False


def sync_time_ntp():
    global _time_synced, _time_sync_error
    if not TIME_SYNC_ENABLE:
        _time_synced = False
        _time_sync_error = "time sync disabled"
        return False, _time_sync_error
    if ntptime is None:
        _time_synced = False
        _time_sync_error = "ntptime unavailable"
        return False, _time_sync_error
    try:
        ntptime.settime()
        _time_synced = True
        _time_sync_error = ""
        return True, ""
    except Exception as e:
        _time_synced = False
        _time_sync_error = str(e)
        return False, _time_sync_error


def is_quiet_now():
    if not QUIET_ENABLE:
        return False
    lt, synced = localtime_now()
    if (not synced) and (not TIME_UNSYNC_FAILSAFE_QUIET):
        return False
    if not lt:
        return False
    hour = lt[3]
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


_time_synced = False
_time_sync_error = "not synced"


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
    time_synced = False
    last_time_sync_ms = time.ticks_ms()
    time_sync_error = "not synced"
    local_hour_cached = -1
    time_sync_interval_ms = TIME_SYNC_EVERY_HOURS * 60 * 60 * 1000
    if TIME_SYNC_ENABLE and wlan.isconnected():
        ok, err = sync_time_ntp()
        time_synced = ok
        time_sync_error = err
        last_time_sync_ms = time.ticks_ms()
    lt_boot, synced_boot = localtime_now()
    if lt_boot:
        local_hour_cached = lt_boot[3]
    print("Time sync:", "OK" if time_synced else "ERR", ("" if time_synced else time_sync_error))
    print("Local time:", _format_local_time(lt_boot), "synced:", "YES" if synced_boot else "NO")
    print("Quiet window: {:02d}:00-{:02d}:00".format(QUIET_START_H, QUIET_END_H))

    scd_i2c_freq = SHARED_I2C_FREQ if SHARED_I2C_BUS else SCD_I2C_FREQ
    i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=scd_i2c_freq)
    scd_scan = [hex(x) for x in i2c_scd.scan()]
    print("SCD I2C scan:", scd_scan)
    if SHARED_I2C_BUS:
        print("I2C mode: shared bus for SCD41+OLED on GPIO{},GPIO{}".format(SCD_SDA, SCD_SCL))

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
    last_i2c_recover = time.ticks_add(time.ticks_ms(), -I2C_RECOVERY_COOLDOWN_MS)
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
                if SHARED_I2C_BUS:
                    i2c_oled = i2c_scd
                    scan = [OLED_ADDR]
                    oled_scan_list = ["shared-bus"]
                    oled_scan = "shared-bus"
                else:
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
                if SHARED_I2C_BUS and time.ticks_diff(now, last_i2c_recover) >= I2C_RECOVERY_COOLDOWN_MS:
                    last_i2c_recover = now
                    try:
                        i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=SHARED_I2C_FREQ)
                        scd.i2c = i2c_scd
                        oled_ok = False
                        print("I2C recover: shared bus reinit after OLED error")
                    except Exception as e2:
                        print("I2C recover error:", e2)

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

                    co2_for_alert = co2 if ALERT_USE_RAW_CO2 else co2_f
                    temp_for_alert = temp if temp is not None else temp_f
                    rh_for_alert = rh if rh is not None else rh_f

                    lvl = level_from_co2(co2_for_alert, warn_on, high_on)
                    if wlan.isconnected() and TG_ENABLE:
                        if (prev_lvl != LVL_HIGH) and (lvl == LVL_HIGH):
                            if is_quiet_now():
                                print("TG quiet: HIGH muted")
                            else:
                                if tg_send_alert(
                                    render_alert_high(co2_for_alert, temp_for_alert, rh_for_alert, reminder=False)
                                ):
                                    print("TG alert: HIGH sent")
                                    last_remind = now
                        elif (lvl == LVL_HIGH) and (time.ticks_diff(now, last_remind) > remind_ms):
                            if not is_quiet_now():
                                if tg_send_alert(
                                    render_alert_high(co2_for_alert, temp_for_alert, rh_for_alert, reminder=True)
                                ):
                                    print("TG alert: HIGH reminder sent")
                                    last_remind = now
                        elif (prev_lvl == LVL_HIGH) and (lvl == LVL_GOOD):
                            if tg_send_alert(
                                render_alert_recovery(co2_for_alert, temp_for_alert, rh_for_alert)
                            ):
                                print("TG alert: GOOD sent")
                    prev_lvl = lvl
            except Exception as e:
                print("Sensor read error:", e)
                if SHARED_I2C_BUS and time.ticks_diff(now, last_i2c_recover) >= I2C_RECOVERY_COOLDOWN_MS:
                    last_i2c_recover = now
                    try:
                        i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=SHARED_I2C_FREQ)
                        scd.i2c = i2c_scd
                        oled_ok = False
                        print("I2C recover: shared bus reinit after sensor error")
                    except Exception as e2:
                        print("I2C recover error:", e2)

        if time.ticks_diff(now, last_ready_log) >= READY_LOG_EVERY_MS:
            last_ready_log = now
            age = "-" if last_sample_ms is None else str(time.ticks_diff(now, last_sample_ms) // 1000)
            lt_dbg, synced_dbg = localtime_now()
            local_hour_cached = lt_dbg[3] if lt_dbg else -1
            if not synced_dbg:
                time_synced = False
            print(
                "ready_raw:", ready_raw,
                "sample_age_s:", age,
                "local_hour:", local_hour_cached,
                "time_synced:", "YES" if time_synced else "NO"
            )

        if time.ticks_diff(now, last_wifi) > WIFI_RECONNECT_MS:
            last_wifi = now
            if not wlan.isconnected():
                wlan = wifi_connect()
                if wlan.isconnected():
                    print("WiFi reconnected")
                    if TIME_SYNC_ENABLE:
                        ok, err = sync_time_ntp()
                        time_synced = ok
                        time_sync_error = err
                        last_time_sync_ms = now
                        lt_sync, synced_sync = localtime_now()
                        local_hour_cached = lt_sync[3] if lt_sync else -1
                        print("Time sync:", "OK" if time_synced else "ERR", ("" if time_synced else time_sync_error))
                        print("Local time:", _format_local_time(lt_sync), "synced:", "YES" if synced_sync else "NO")

        if TIME_SYNC_ENABLE and wlan.isconnected() and time.ticks_diff(now, last_time_sync_ms) >= time_sync_interval_ms:
            ok, err = sync_time_ntp()
            time_synced = ok
            time_sync_error = err
            last_time_sync_ms = now
            lt_sync, synced_sync = localtime_now()
            local_hour_cached = lt_sync[3] if lt_sync else -1
            print("Periodic time sync:", "OK" if time_synced else "ERR", ("" if time_synced else time_sync_error))
            print("Local time:", _format_local_time(lt_sync), "synced:", "YES" if synced_sync else "NO")

        if wlan.isconnected() and TG_CMDS_ENABLE and time.ticks_diff(now, last_cmd) > TG_CMD_POLL_MS:
            last_cmd = now
            age_s = "-" if last_sample_ms is None else str(time.ticks_diff(now, last_sample_ms) // 1000)
            uptime_s = time.ticks_diff(now, boot_ms) // 1000
            sensor_ok = bool(last_sample_ms is not None and time.ticks_diff(now, last_sample_ms) <= SCD_RESTART_MS)
            lt_cmd, synced_cmd = localtime_now()
            local_time_txt = _format_local_time(lt_cmd)
            if not synced_cmd:
                time_synced = False
            warn_on, high_on, remind_min = tg_poll_commands(
                co2, temp, rh, co2_f, temp_f, rh_f,
                age_s, sensor_ok, wlan.isconnected(), uptime_s, remind_ms, ",".join(scd_scan), oled_scan, state,
                time_synced, time_sync_error, local_time_txt, is_quiet_now()
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
