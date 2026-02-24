from machine import Pin, I2C
import time

from config import (
    SCD4X_ADDR,
    OLED_ADDR,
    SCD_SDA,
    SCD_SCL,
    OLED_SDA,
    OLED_SCL,
    SHARED_I2C_BUS,
    SHARED_I2C_FREQ,
    I2C_RECOVERY_COOLDOWN_MS,
    SCD_I2C_FREQ,
    OLED_I2C_FREQ,
)


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


def create_scd_i2c():
    scd_i2c_freq = SHARED_I2C_FREQ if SHARED_I2C_BUS else SCD_I2C_FREQ
    return I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=scd_i2c_freq)


def scan_hex(i2c):
    return [hex(x) for x in i2c.scan()]


def get_oled_probe(i2c_scd):
    if SHARED_I2C_BUS:
        return i2c_scd, [OLED_ADDR], ["shared-bus"], "shared-bus"
    i2c_oled = I2C(1, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=OLED_I2C_FREQ)
    scan = i2c_oled.scan()
    scan_list = [hex(x) for x in scan]
    return i2c_oled, scan, scan_list, ",".join(scan_list)


def recover_shared_i2c(now, last_i2c_recover, scd):
    if not SHARED_I2C_BUS:
        return scd.i2c, last_i2c_recover, False
    if time.ticks_diff(now, last_i2c_recover) < I2C_RECOVERY_COOLDOWN_MS:
        return scd.i2c, last_i2c_recover, False
    i2c_scd = I2C(0, sda=Pin(SCD_SDA), scl=Pin(SCD_SCL), freq=SHARED_I2C_FREQ)
    scd.i2c = i2c_scd
    return i2c_scd, now, True
