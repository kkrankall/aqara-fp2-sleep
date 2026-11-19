"""Constants for the Aqara FP2 Sleep integration."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "fp2_sleep"
LOGGER = logging.getLogger(__package__)

CONF_REGION = "region"
CONF_ACCOUNT = "account"
CONF_PASSWORD_MD5 = "password_md5"
CONF_PHONE_ID = "phone_id"
CONF_DEVICE_ID = "device_id"
# Option: a local presence sensor (e.g. the FP2 over HomeKit) to poll by.
CONF_PRESENCE_ENTITY = "presence_entity"

# The FP2's model id in Aqara's device list.
FP2_MODEL = "lumi.motion.agl001"

# Entity ids start with this, matching the SleepRadar app's MQTT entities, so
# history, statistics and dashboards carry over when switching.
ENTITY_PREFIX = "aqara_fp2_sleep"

# Readings asked for on every poll (Aqara's res/query names).
ATTR_HEART_RATE = "heartrate_value"
ATTR_BREATHING = "respiration_rate_value"
ATTR_SLEEP_STATE = "sleep_state"
ATTR_MOVEMENT = "body_movement_value"
ATTR_LUX = "lux"
ATTR_MODE = "set_device_mode4"
ATTR_ONLINE = "device_offline_status"
QUERY_ATTRS = (
    ATTR_HEART_RATE,
    ATTR_BREATHING,
    ATTR_SLEEP_STATE,
    ATTR_MOVEMENT,
    ATTR_LUX,
    ATTR_MODE,
    ATTR_ONLINE,
)

# The history log takes numeric resource ids instead of names.
SLEEP_STATE_RESOURCE = "13.106.85"

# Aqara's sleep-state codes (community mapping, matches the app's report).
STAGES = {
    "0": "out_of_bed",
    "1": "in_bed",
    "2": "awake",
    "3": "rem",
    "4": "light",
    "5": "deep",
}
ASLEEP_CODES = frozenset({"3", "4", "5"})

# FP2 work modes. Vitals are only measured in sleep monitoring.
MODES = {"3": "zone_detection", "5": "fall_detection", "9": "sleep_monitoring"}
SLEEP_MODE = "9"
# Despite its name, device_offline_status reads 1 while the FP2 is online.
ONLINE_CODE = "1"
OFFLINE_CODE = "0"

# A night runs from this local hour to the same hour the next day.
NIGHT_START_HOUR = 18
# Without a presence sensor: poll every minute through the night or while
# someone is in bed, else every five minutes. The FP2 reports vitals about
# every 6 s.
FAST_INTERVAL = timedelta(seconds=60)
SLOW_INTERVAL = timedelta(minutes=5)
FAST_HOURS_END = 12  # fast polling runs NIGHT_START_HOUR..FAST_HOURS_END
# With a presence sensor: every minute while someone is there and for
# PRESENCE_HOLD after they leave, else every IDLE_INTERVAL.
PRESENCE_HOLD = timedelta(minutes=30)
IDLE_INTERVAL = timedelta(minutes=30)
# The night's stage changes are re-read this often (two requests each time).
NIGHT_REFRESH = timedelta(minutes=5)
# Device name, firmware and room are re-read this often.
DEVICE_REFRESH = timedelta(hours=6)
