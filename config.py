try:
    import secrets
except ImportError:
    secrets = None

# ===================== Hardware =====================
SCD4X_ADDR = 0x62
OLED_ADDR = 0x3C

SCD_SDA = 8
SCD_SCL = 9
OLED_SDA = 8
OLED_SCL = 9
SHARED_I2C_BUS = (SCD_SDA == OLED_SDA) and (SCD_SCL == OLED_SCL)
SHARED_I2C_FREQ = 20_000
I2C_RECOVERY_COOLDOWN_MS = 2500

W = 128
H = 64

# Lower OLED bus speed for better stability with shared 3V3 rail noise
SCD_I2C_FREQ = 50_000
OLED_I2C_FREQ = 100_000

# Accuracy/config per SCD41 datasheet
# Max-accuracy profile defaults: keep ASC enabled and target fresh-air baseline.
ASC_ENABLED = True
ASC_TARGET_PPM = 420           # None = keep sensor default target
AMBIENT_PRESSURE_PA = 101000
ALTITUDE_M = 0                 # used when AMBIENT_PRESSURE_PA is None
TEMP_OFFSET_C = getattr(secrets, "TEMP_OFFSET_C", None) if secrets else None  # internal SCD temp offset (0..20C)
PERSIST_SETTINGS = False       # write settings to sensor EEPROM (use sparingly)
ALERT_USE_RAW_CO2 = True       # raw CO2 for threshold logic/alerts, EMA remains for UI

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
TG_TEXT_MAX_LEN = 3800

# Time sync / timezone (Riga default)
TIME_SYNC_ENABLE = True
TIME_SYNC_EVERY_HOURS = 6
TZ_OFFSET_MIN = 120
DST_ENABLE = True
DST_REGION = "EU"
TIME_UNSYNC_FAILSAFE_QUIET = False

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
