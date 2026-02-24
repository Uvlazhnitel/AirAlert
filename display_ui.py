import framebuf

from config import (
    W,
    H,
    UI_BLINK_HIGH,
    UI_SHOW_TREND,
    UI_CO2_BAR_MIN,
    UI_CO2_BAR_MAX,
)


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


def draw_screen(
    oled, screen_idx, co2, temp, rh, age_s, warn_on, high_on,
    trend_dir=0, temp_trend_dir=0, rh_trend_dir=0, high_blink_phase=False
):
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
