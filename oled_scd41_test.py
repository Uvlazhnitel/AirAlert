from machine import Pin, I2C
import time

try:
    import sh1106
except ImportError:
    raise ImportError("sh1106.py not found on device. Copy sh1106.py to board first.")

SCD4X_ADDR = 0x62
OLED_ADDR = 0x3C

SCD_SDA = 8
SCD_SCL = 9
OLED_SDA = 13
OLED_SCL = 12

W = 128
H = 64


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


def draw_lines(oled, lines):
    oled.fill(0)
    y = 0
    for line in lines[:6]:
        oled.text(line[:21], 0, y, 1)
        y += 10
    oled.show()


def main():
    print("=== OLED + SCD41 test ===")

    i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=50_000)
    i2c_oled = I2C(1, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=400_000)

    scd_scan = [hex(x) for x in i2c_scd.scan()]
    oled_scan = [hex(x) for x in i2c_oled.scan()]
    print("SCD I2C scan:", scd_scan)
    print("OLED I2C scan:", oled_scan)

    oled = sh1106.SH1106_I2C(W, H, i2c_oled, addr=OLED_ADDR)
    oled.sleep(False)
    draw_lines(oled, ["Boot...", "SCD scan: " + (scd_scan[0] if scd_scan else "-"), "OLED scan: " + (oled_scan[0] if oled_scan else "-")])

    if SCD4X_ADDR not in i2c_scd.scan():
        print("SCD41 not found")
        draw_lines(oled, ["ERROR", "SCD41 not found", "Check GPIO8/9"])
        return

    if OLED_ADDR not in i2c_oled.scan():
        print("OLED not found")
        return

    scd = SCD41(i2c_scd)
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

    scd.start_periodic_measurement()

    draw_lines(oled, ["Warming up...", "Waiting sample..."])
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 20_000:
        try:
            raw = scd.get_data_ready_raw()
            ready = (raw & 0x07FF) != 0
            print("ready_raw:", raw, "ready:", ready)
            if ready:
                co2, temp, rh = scd.read_measurement()
                msg = "CO2={} T={:.1f} RH={:.1f}".format(co2, temp, rh)
                print(msg)
                draw_lines(
                    oled,
                    [
                        "SCD41 OK",
                        "CO2: {} ppm".format(co2),
                        "T: {:.1f} C".format(temp),
                        "RH: {:.1f} %".format(rh),
                    ],
                )
                break
            draw_lines(oled, ["Warming up...", "ready_raw: {}".format(raw)])
        except Exception as e:
            print("poll/read error:", e)
            draw_lines(oled, ["Read error", str(e)])
        time.sleep_ms(500)

    while True:
        try:
            if scd.get_data_ready_status():
                co2, temp, rh = scd.read_measurement()
                print("CO2:{} T:{:.2f} RH:{:.2f}".format(co2, temp, rh))
                draw_lines(
                    oled,
                    [
                        "SCD41 LIVE",
                        "CO2: {} ppm".format(co2),
                        "T: {:.1f} C".format(temp),
                        "RH: {:.1f} %".format(rh),
                    ],
                )
        except Exception as e:
            print("loop error:", e)
            draw_lines(oled, ["Loop error", str(e)])
        time.sleep_ms(1000)


main()
