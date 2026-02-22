from machine import Pin, I2C
import time
import framebuf
import sh1106

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

# Display refresh rate
UI_REFRESH_MS = 1200
SCREEN_SWITCH_MS = 5000
READY_LOG_EVERY_MS = 3000
SCD_RESTART_MS = 30000


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
    if co2 < 1200:
        return "OK"
    if co2 < 1800:
        return "HIGH"
    return "ALERT"


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

    i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=SCD_I2C_FREQ)
    print("SCD I2C scan:", [hex(x) for x in i2c_scd.scan()])

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

    try:
        scd.start_periodic_measurement()
    except Exception as e:
        print("SCD start failed:", e)
        return

    last_ui = time.ticks_add(time.ticks_ms(), -UI_REFRESH_MS)
    last_screen_switch = time.ticks_ms()
    last_ready_log = time.ticks_add(time.ticks_ms(), -READY_LOG_EVERY_MS)
    last_sample_ms = None
    co2 = None
    temp = None
    rh = None
    warm_start = time.ticks_ms()
    last_restart = warm_start
    ready_raw = 0
    screen = 0

    while True:
        now = time.ticks_ms()

        try:
            ready_raw = scd.get_data_ready_raw()
            ready = (ready_raw & 0x07FF) != 0
            if ready:
                co2, temp, rh = scd.read_measurement()
                last_sample_ms = now
                print("CO2:{} ppm  T:{:.2f} C  RH:{:.2f}%".format(co2, temp, rh))
                if not oled_ok:
                    try:
                        i2c_oled = I2C(1, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=OLED_I2C_FREQ)
                        print("OLED I2C scan:", [hex(x) for x in i2c_oled.scan()])
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

        if (co2 is None) or (last_sample_ms is None):
            if time.ticks_diff(now, last_restart) >= SCD_RESTART_MS:
                print("No sample yet, restarting SCD41 measurement")
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
        if (co2 is None) or (last_sample_ms is None):
            time.sleep_ms(120)
            continue

        if oled_ok and time.ticks_diff(now, last_ui) >= UI_REFRESH_MS:
            if time.ticks_diff(now, last_screen_switch) >= SCREEN_SWITCH_MS:
                last_screen_switch = now
                screen = (screen + 1) % 3
            last_ui = now
            age = time.ticks_diff(now, last_sample_ms) // 1000
            draw_screen(oled, screen, co2, temp, rh, age)

        time.sleep_ms(120)


main()
