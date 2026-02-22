from machine import Pin, I2C
import time

SCD4X_ADDR = 0x62
SCD_SDA = 8
SCD_SCL = 9


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


def main():
    print("=== SCD41 bare test ===")
    i2c = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=50_000)
    scan = i2c.scan()
    print("I2C scan:", [hex(x) for x in scan])
    if SCD4X_ADDR not in scan:
        print("SCD41 not found at 0x62")
        return

    scd = SCD41(i2c)
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
        print("start_periodic_measurement: OK")
    except Exception as e:
        print("start_periodic_measurement error:", e)
        return

    print("Waiting up to 20s for first sample...")
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 20_000:
        try:
            raw = scd.get_data_ready_raw()
            ready = (raw & 0x07FF) != 0
            print("ready_raw:", raw, "ready:", ready)
            if ready:
                co2, temp, rh = scd.read_measurement()
                print("MEASUREMENT OK -> CO2:", co2, "ppm  T:", round(temp, 2), "C  RH:", round(rh, 2), "%")
                return
        except Exception as e:
            print("poll/read error:", e)
        time.sleep_ms(500)

    print("FAIL: no sample in 20s (ready_raw stayed 0 or read failed)")


main()
