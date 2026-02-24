# ESP32-S3 Air Monitor (SCD41 + SH1106 + Telegram)

Production-style indoor air monitor for `ESP32-S3` with:
- `SCD41` CO2/temperature/humidity sensing
- `SH1106 128x64` OLED infographic UI
- Telegram alerts and command interface
- time-aware quiet hours with NTP sync
- runtime threshold persistence in `state.json`

---

## 1. What this project does

The firmware continuously reads air metrics, renders them on OLED, and sends Telegram notifications when air quality becomes poor.

Core behavior:
- warmup screen until first valid SCD41 sample
- rotating OLED screens: `CO2`, `TEMP`, `HUM`
- stale-data screen if no fresh sample for configured timeout
- Telegram alerts:
  - `HIGH` entry alert
  - periodic reminder while still high
  - recovery alert when back to good
- command-only Telegram UX (inline buttons disabled)

---

## 2. Current architecture (A/B/C/D refactor)

The project is split into modules:

- `main.py`
  - application orchestration loop
  - Wi-Fi lifecycle
  - time sync + quiet logic integration
  - sensor sampling pipeline
  - calls into UI and Telegram modules

- `config.py`
  - all constants/config flags
  - pin mapping, thresholds, UI settings, Telegram settings

- `state_store.py`
  - state persistence (`state.json`)
  - validation and default merge for thresholds/reminder

- `sensor_i2c.py`
  - `SCD41` driver (CRC, commands, read/convert)
  - shared/separate I2C helpers
  - shared-bus recovery helper

- `display_ui.py`
  - OLED rendering functions
  - trend helpers and screen drawing

- `telegram_bot.py`
  - Telegram transport (`sendMessage`, polling)
  - command routing
  - command text renderers
  - alert text templates

- `sh1106.py`
  - OLED driver dependency

- `secrets.py`
  - local private credentials (Wi-Fi, Telegram)

---

## 3. Hardware

### 3.1 Supported board and devices

- board: `ESP32-S3`
- sensor: `SCD41` (`0x62`)
- display: `SH1106 128x64` (`0x3C`)

### 3.2 Current pin mapping (as configured now)

Current `config.py` is set to **shared I2C bus**:

- `SCD41 SDA -> GPIO8`
- `SCD41 SCL -> GPIO9`
- `OLED  SDA -> GPIO8`
- `OLED  SCL -> GPIO9`

### 3.3 Electrical requirements (important)

- common `GND` for ESP32 + SCD41 + OLED is mandatory
- stable `3.3V` rail is strongly recommended
- short I2C wires reduce random `ENODEV/ETIMEDOUT`
- power quality strongly affects stability (PC USB often cleaner than cheap adapters)

---

## 4. Telegram setup

### 4.1 `secrets.py` template

```python
WIFI_SSID = "YOUR_WIFI"
WIFI_PASS = "YOUR_PASS"

TG_TOKEN = "YOUR_BOT_TOKEN"
TG_CHAT_ID = 123456789
TG_ALLOWED_USER_ID = 123456789

# Optional high-accuracy env settings
# AMBIENT_PRESSURE_PA = 101000
# TEMP_OFFSET_C = 2.0
```

Notes:
- `TG_CHAT_ID` is target for proactive alerts.
- command replies are routed to active chat from incoming updates.
- if token was leaked, rotate it in `@BotFather` immediately.

### 4.2 Supported Telegram commands

- `/menu` - command index
- `/status` - compact status
- `/info` - full diagnostics
- `/thresholds` - WARN/HIGH view
- `/settings` - WARN/HIGH/REM view
- `/help` - command help

Inline keyboard mode is currently disabled by config:
- `TG_INLINE_KEYBOARD_ENABLE = False`

---

## 5. Accuracy profile and measurement behavior

Current profile in `config.py`:
- `ASC_ENABLED = True`
- `ASC_TARGET_PPM = 420`
- `ALERT_USE_RAW_CO2 = True`
- `AMBIENT_PRESSURE_PA = 101000` (configured)

Meaning:
- long-term drift compensation is enabled (ASC)
- alerts use raw CO2 (not smoothed), so alarm timing is more truthful
- UI still uses EMA-filtered values for visual stability

For best long-term accuracy:
- expose sensor to fresh air regularly (daily ventilation)
- keep pressure reasonably correct (or update periodically)
- calibrate `TEMP_OFFSET_C` with a nearby reference thermometer

---

## 6. Time sync and quiet hours

Time features:
- NTP sync via `ntptime`
- timezone offset and EU DST handling
- quiet window suppression for alerts

Key config:
- `TIME_SYNC_ENABLE`, `TIME_SYNC_EVERY_HOURS`
- `TZ_OFFSET_MIN = 120`, `DST_ENABLE = True`, `DST_REGION = "EU"`
- `QUIET_START_H`, `QUIET_END_H`
- `TIME_UNSYNC_FAILSAFE_QUIET = False`

Fail-safe behavior:
- if time is unsynced and fail-safe is `False`, quiet mode is not applied (alerts are not muted by uncertain clock)

---

## 7. Deploy and run

### 7.1 Upload all required files

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/main.py :main.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/config.py :config.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/state_store.py :state_store.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/sensor_i2c.py :sensor_i2c.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/display_ui.py :display_ui.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/telegram_bot.py :telegram_bot.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/sh1106.py :sh1106.py
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/secrets.py :secrets.py
```

### 7.2 Start firmware

Recommended:
```bash
mpremote connect /dev/cu.usbmodemXXXX reset
```

Live logs:
```bash
mpremote connect /dev/cu.usbmodemXXXX repl
```

Notes:
- after `reset`, firmware auto-starts from `main.py`
- avoid chaining `reset` + `run main.py` in one shot to prevent raw REPL race errors

---

## 8. Runtime logs to expect

Typical healthy boot:
- `WiFi connected: True`
- `Time sync: OK`
- `SCD I2C scan: ['0x3c', '0x62']` (shared bus)
- `OLED init: OK`
- periodic `RAW CO2... | FILT CO2...`

If quiet/time issues:
- check `Local time: ... synced: YES/NO`
- check `ready_raw ... local_hour ... time_synced ...`

---

## 9. Troubleshooting

### 9.1 `OLED init error: ENODEV/ETIMEDOUT`

Usually electrical:
- verify common ground
- improve power source/cable
- shorten I2C wires
- consider separate I2C buses if environment is noisy

### 9.2 `STALE` after some runtime

Most often caused by:
- transient I2C/power faults
- unstable adapter/cable

Checks:
- review logs for `Sensor read error`
- verify no power dips
- compare behavior on PC USB vs wall adapter

### 9.3 Telegram `Bad Request: chat not found`

- verify bot token and target chat belong to same bot
- open chat with bot and send `/start`
- verify bot can message that chat (private/group context)
- rotate token if previously exposed

### 9.4 Telegram `Bad Request: chat_id is empty`

This project already uses form-encoded transport in `telegram_bot.py` to avoid JSON body issues on some MicroPython builds.
If seen again, capture full serial line and inspect transport path.

### 9.5 `mpremote ... could not enter raw repl`

Use:
- upload files
- `reset`
- then optionally open `repl`

Do not immediately run another `run main.py` after reset in same chain.

---

## 10. Diagnostics scripts

### 10.1 SCD41 only

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/scd41_test.py :scd41_test.py
mpremote connect /dev/cu.usbmodemXXXX run scd41_test.py
```

### 10.2 SCD41 + OLED

```bash
mpremote connect /dev/cu.usbmodemXXXX fs cp /Users/uvlazhnitel/Documents/coding/esp32/oled_scd41_test.py :oled_scd41_test.py
mpremote connect /dev/cu.usbmodemXXXX run oled_scd41_test.py
```

---

## 11. Configuration quick map

Most-edited config keys in `config.py`:
- thresholds and reminders: `DEFAULT_WARN_ON`, `DEFAULT_HIGH_ON`, `DEFAULT_REMIND_MIN`
- quiet mode: `QUIET_ENABLE`, `QUIET_START_H`, `QUIET_END_H`
- stale handling: `UI_STALE_SEC`, `SCD_RESTART_MS`, `SCD_FAST_RECOVERY_MS`
- UI behavior: `SCREEN_SWITCH_MS`, `UI_MODE`, `UI_SHOW_TREND`
- hardware mode: `SCD_SDA/SCL`, `OLED_SDA/SCL`, `SHARED_I2C_BUS`

---

## 12. Security and operational notes

- never commit real `secrets.py`
- rotate Telegram token immediately if shared in chat/screenshots
- keep one known-good cable/adapter as baseline during debugging
- for changes with hardware impact, run at least 30-minute soak test
