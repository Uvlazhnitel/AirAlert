from machine import Pin, I2C
import time
import framebuf
import sh1106
import network
import socket

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

SCD_SDA = 8
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
SCD_RESTART_MS = 90000

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

LVL_GOOD = 0
LVL_OK = 1
LVL_HIGH = 2


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


def level_from_co2(co2):
    if co2 < 800:
        return LVL_GOOD
    if co2 <= 1500:
        return LVL_OK
    return LVL_HIGH


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


def tg_send(text):
    global _last_tg_send

    if (not TG_ENABLE) or (not TG_TOKEN) or (TG_CHAT_ID == 0):
        return False
    if requests is None:
        return False

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_tg_send) < TG_MIN_GAP_MS:
        return False

    url = "https://api.telegram.org/bot{}/sendMessage?chat_id={}&text={}".format(
        TG_TOKEN, TG_CHAT_ID, url_escape(text)
    )
    r = None
    try:
        r = requests.get(url)
        code = getattr(r, "status_code", None)
        if code is not None and code != 200:
            print("TG HTTP:", code)
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


def status_text(co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok):
    lvl = "-" if co2_f is None else quality_label(co2_f)
    return (
        "Mode: HOME\n"
        "CO2: {} ppm ({})\n"
        "T: {} C\n"
        "RH: {} %\n"
        "Sample age: {} s\n"
        "Sensor: {}\n"
        "Wi-Fi: {}"
    ).format(
        "-" if co2_f is None else int(round(co2_f)),
        lvl,
        "-" if temp_f is None else "{:.1f}".format(temp_f),
        "-" if rh_f is None else "{:.1f}".format(rh_f),
        sample_age_s,
        "OK" if sensor_ok else "ERR",
        "OK" if wifi_ok else "ERR",
    )


def info_text(co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan):
    lvl = "-" if co2_f is None else quality_label(co2_f)
    return (
        "CO2 Monitor Info\n"
        "Mode: HOME\n"
        "Wi-Fi: {}\n"
        "Sensor: {}\n"
        "Level: {}\n"
        "Raw: CO2={} T={} RH={}\n"
        "Filt: CO2={} T={} RH={}\n"
        "Sample age: {} s\n"
        "Uptime: {} s\n"
        "Thresholds: GOOD<800 OK<=1500 HIGH>1500\n"
        "Reminder: {} min\n"
        "I2C SCD: {}\n"
        "I2C OLED: {}"
    ).format(
        "OK" if wifi_ok else "ERR",
        "OK" if sensor_ok else "ERR",
        lvl,
        "-" if co2_raw is None else int(round(co2_raw)),
        "-" if temp_raw is None else "{:.2f}".format(temp_raw),
        "-" if rh_raw is None else "{:.2f}".format(rh_raw),
        "-" if co2_f is None else int(round(co2_f)),
        "-" if temp_f is None else "{:.2f}".format(temp_f),
        "-" if rh_f is None else "{:.2f}".format(rh_f),
        sample_age_s,
        uptime_s,
        remind_ms // 60000,
        scd_scan,
        oled_scan,
    )


def tg_poll_commands(
    co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
    sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan
):
    global _last_update_id

    if (not TG_CMDS_ENABLE) or (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return

    data = tg_get_updates(_last_update_id + 1)
    if not data or not data.get("ok"):
        return

    for upd in data.get("result", []):
        uid = upd.get("update_id", 0)
        if uid > _last_update_id:
            _last_update_id = uid

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

        if cmd == "status":
            tg_send(status_text(co2_f, temp_f, rh_f, sample_age_s, sensor_ok, wifi_ok))
        elif cmd == "info":
            tg_send(info_text(
                co2_raw, temp_raw, rh_raw, co2_f, temp_f, rh_f,
                sample_age_s, sensor_ok, wifi_ok, uptime_s, remind_ms, scd_scan, oled_scan
            ))
        elif cmd == "help":
            tg_send("Commands:\n/status - compact status\n/info - detailed status\n/help - command list")


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


def quality_label(co2):
    if co2 < 800:
        return "GOOD"
    if co2 <= 1500:
        return "OK"
    return "HIGH"


def quality_fill_px(co2):
    # map 400..2400 ppm -> 0..120 px
    c = co2
    if c < 400:
        c = 400
    if c > 2400:
        c = 2400
    return int((c - 400) * 120 / 2000)


def draw_header(oled, title, age_s):
    oled.fill_rect(0, 0, 128, 10, 1)
    oled.text(title, 2, 1, 0)
    oled.text("{}s".format(age_s), 104, 1, 0)


def draw_footer(oled, co2):
    oled.rect(4, 54, 120, 8, 1)
    fill = quality_fill_px(co2)
    if fill > 0:
        oled.fill_rect(4, 54, fill, 8, 1)


def draw_co2_screen(oled, co2, age_s):
    oled.fill(0)

    label = quality_label(co2)
    draw_header(oled, "CO2", age_s)
    oled.text(label, 48, 1, 0)

    co2s = str(int(round(co2)))
    sc = 3 if len(co2s) <= 3 else 2
    x = centered_x(co2s, sc)
    y = 14 if sc == 3 else 18
    text_scaled(oled, co2s, x, y, sc)
    oled.text("ppm", 98, 34, 1)

    draw_footer(oled, co2)
    oled.show()


def draw_temp_screen(oled, temp, age_s):
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
    oled.show()


def draw_hum_screen(oled, rh, age_s):
    oled.fill(0)
    draw_header(oled, "HUM", age_s)

    hs = "{:.0f}".format(rh)
    sc = 5 if len(hs) <= 2 else 4
    x = centered_x(hs, sc)
    y = 16 if sc == 5 else 18
    text_scaled(oled, hs, x, y, sc)
    oled.text("%", 116, 40, 1)
    oled.show()


def draw_screen(oled, screen_idx, co2, temp, rh, age_s):
    if screen_idx == 0:
        draw_co2_screen(oled, co2, age_s)
    elif screen_idx == 1:
        draw_temp_screen(oled, temp, age_s)
    else:
        draw_hum_screen(oled, rh, age_s)


def draw_warmup(oled):
    oled.fill(0)
    oled.text("SCD41 WARMUP", 16, 20, 1)
    oled.text("Waiting first sample", 0, 34, 1)
    oled.show()


def draw_error(oled, line1, line2=""):
    oled.fill(0)
    oled.text("ERROR", 44, 6, 1)
    oled.text(line1[:21], 0, 24, 1)
    oled.text(line2[:21], 0, 36, 1)
    oled.show()


# ===================== Main =====================
def main():
    print("=== CO2 OLED Monitor ===")
    boot_ms = time.ticks_ms()

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

    last_ui = time.ticks_add(time.ticks_ms(), -UI_REFRESH_MS)
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
    ready_raw = 0
    screen = 0
    prev_lvl = LVL_GOOD
    last_remind = time.ticks_add(time.ticks_ms(), -TG_REMIND_MS)
    oled_scan = "-"

    while True:
        now = time.ticks_ms()

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
                    last_sample_ms = now
                    print("RAW CO2:{} T:{:.2f} RH:{:.2f} | FILT CO2:{} T:{:.2f} RH:{:.2f}".format(
                        co2, temp, rh, int(round(co2_f)), temp_f, rh_f
                    ))

                    lvl = level_from_co2(co2_f)
                    if wlan.isconnected() and TG_ENABLE:
                        if (prev_lvl != LVL_HIGH) and (lvl == LVL_HIGH):
                            if tg_send_alert(
                                "Ventilate now\nCO2: {} ppm\nT: {:.1f} C\nRH: {:.1f}%".format(
                                    int(round(co2_f)), temp_f, rh_f
                                )
                            ):
                                print("TG alert: HIGH sent")
                                last_remind = now
                        elif (lvl == LVL_HIGH) and (time.ticks_diff(now, last_remind) > TG_REMIND_MS):
                            if tg_send_alert(
                                "Reminder: ventilate\nCO2: {} ppm\nT: {:.1f} C\nRH: {:.1f}%".format(
                                    int(round(co2_f)), temp_f, rh_f
                                )
                            ):
                                print("TG alert: HIGH reminder sent")
                                last_remind = now
                        elif (prev_lvl == LVL_HIGH) and (lvl == LVL_GOOD):
                            if tg_send_alert(
                                "Air is back to normal\nCO2: {} ppm\nT: {:.1f} C\nRH: {:.1f}%".format(
                                    int(round(co2_f)), temp_f, rh_f
                                )
                            ):
                                print("TG alert: GOOD sent")
                    prev_lvl = lvl

                    if not oled_ok:
                        try:
                            i2c_oled = I2C(1, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=OLED_I2C_FREQ)
                            oled_scan_list = [hex(x) for x in i2c_oled.scan()]
                            oled_scan = ",".join(oled_scan_list)
                            print("OLED I2C scan:", oled_scan_list)
                            if OLED_ADDR in i2c_oled.scan():
                                oled = sh1106.SH1106_I2C(W, H, i2c_oled, addr=OLED_ADDR)
                                oled.sleep(False)
                                oled_ok = True
                                print("OLED init: OK")
                            else:
                                print("OLED not found at 0x3C")
                        except Exception as e:
                            print("OLED init error:", e)
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
            tg_poll_commands(
                co2, temp, rh, co2_f, temp_f, rh_f,
                age_s, sensor_ok, wlan.isconnected(), uptime_s, TG_REMIND_MS, ",".join(scd_scan), oled_scan
            )

        stale = False
        if last_sample_ms is not None and time.ticks_diff(now, last_sample_ms) > SCD_RESTART_MS:
            stale = True

        if (co2 is None) or (last_sample_ms is None) or stale:
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

        # Important for stability: do not touch OLED while waiting first sample.
        if (co2_f is None) or (last_sample_ms is None):
            time.sleep_ms(120)
            continue

        if oled_ok and time.ticks_diff(now, last_ui) >= UI_REFRESH_MS:
            if time.ticks_diff(now, last_screen_switch) >= SCREEN_SWITCH_MS:
                last_screen_switch = now
                screen = (screen + 1) % 3
            last_ui = now
            age = time.ticks_diff(now, last_sample_ms) // 1000
            draw_screen(oled, screen, co2_f, temp_f, rh_f, age)

        time.sleep_ms(50)


main()
