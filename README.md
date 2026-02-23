# ESP32-S3 CO2 Monitor (SCD41 + SH1106 + Telegram)

Simple local air-quality monitor for ESP32-S3:
- Reads `CO2`, `temperature`, and `humidity` from `SCD41`
- Shows data on `SH1106` OLED (`128x64`) with 3 rotating screens
- Sends Telegram alerts when ventilation is needed
- Supports Telegram control commands and inline settings buttons

## Hardware

- Board: `ESP32-S3`
- Sensor: `SCD41` (I2C0)
- Display: `SH1106 128x64` (I2C1)

### Pin mapping

- `SCD41 SDA -> GPIO3`
- `SCD41 SCL -> GPIO9`
- `OLED SDA -> GPIO13`
- `OLED SCL -> GPIO12`

I2C addresses:
- `SCD41`: `0x62`
- `SH1106`: `0x3C`

## Features

- Stable startup sequence for SCD41
- Sensor auto-recovery if data becomes stale
- Infographic OLED UI with three pages (CO2 / TEMP / HUM), auto-switch every 5s
- CO2 main page with large value, trend marker, and progress bar
- OLED warmup screen before first sample; stale-data screen when samples are old
- CO2 levels:
  - `GOOD`: `< 800 ppm`
  - `OK`: `800..1500 ppm`
  - `HIGH`: `> 1500 ppm`
- Telegram notifications:
  - On entering `HIGH`: `Ventilate now`
  - Reminder every 20 minutes while still `HIGH`
  - On return to `GOOD`: `Air is back to normal`

## Project files

- `main.py` — main app
- `sh1106.py` — OLED driver
- `secrets.py` — local credentials/tokens (do not commit real values)
- `state.json` — persisted runtime settings (created automatically)
- `scd41_test.py` — bare SCD41 diagnostic
- `oled_scd41_test.py` — combined OLED+SCD41 diagnostic

## `secrets.py`

Create or edit `secrets.py`:

```python
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASS = "YOUR_WIFI_PASSWORD"

TG_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TG_CHAT_ID = 123456789
TG_ALLOWED_USER_ID = 123456789
```

## Flash & run

Copy files to ESP32 and start:

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/main.py :main.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/sh1106.py :sh1106.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/secrets.py :secrets.py
mpremote connect /dev/cu.usbmodemXXXX run main.py
```

## Diagnostics

### SCD41 only test

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/scd41_test.py :scd41_test.py
mpremote connect /dev/cu.usbmodemXXXX run scd41_test.py
```

### SCD41 + OLED test

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/oled_scd41_test.py :oled_scd41_test.py
mpremote connect /dev/cu.usbmodemXXXX run oled_scd41_test.py
```

## Troubleshooting

- `Wifi Internal State Error`:
  - Reboot board, keep one stable USB cable/port
  - Current code already performs STA reset + reconnect attempts

- OLED works but SCD41 stays in warmup:
  - Check power stability and common GND
  - Keep short I2C wires
  - Use good USB power/cable

- Telegram messages do not arrive:
  - Verify `TG_TOKEN` and `TG_CHAT_ID`
  - Start chat with bot once manually
  - Check serial logs for `TG HTTP` / `TG send error`

## Telegram commands

- `/status` — compact live status
- `/info` — extended status (raw/filtered values, uptime, thresholds, reminder)
- `/settings` — open threshold/reminder control
- `/thresholds` — alias of `/settings`
- `/help` — command list

### Inline settings buttons

- `WARN -50 / +50`
- `HIGH -50 / +50`
- `REM -5 / +5` (minutes)
- `Preset Home` (`WARN=800`, `HIGH=1500`, `REM=20`)
- `Preset Office` (`WARN=900`, `HIGH=1400`, `REM=15`)

All changes are validated and saved to `state.json`, so they survive reboot.

## Notes

- This project is optimized for indoor usage (`ASC` disabled in config).
- If your bot token was exposed, rotate it in BotFather and update `secrets.py`.
