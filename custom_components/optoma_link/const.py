"""Protocol-level constants for Optoma Link.

Model-specific command tables live in ``projectors/*.json`` (see
``profiles.py``), not here -- this module only holds the things that are
true for every Optoma projector that speaks this ASCII protocol.

Command template: ``~{projector_id}{code} {value}\\r``
Write (set) replies: ``P`` (pass) or ``F`` (fail/unsupported).
Read (query) replies: ``Ok`` followed by the requested value.

Some projectors require a password to be appended after the value
(``~nnnn``, e.g. ``~XX00 1 ~1234``) when RS232 security is enabled in their
OSD. Optoma's documentation only specifies this explicitly for the Power
On command, but several projectors apply it more broadly once security is
turned on, so the client retries any failed command once with the password
suffix attached if one is configured.
"""
from __future__ import annotations

DOMAIN = "optoma_link"
MANUFACTURER = "Optoma"

# --- Diagnostic build: granular poll controls -----------------------------
# This test build (branch ``test/no-polling``) polls the projector ONLY for the
# groups enabled below. Everything else is never queried -- not on a timer, not
# at setup, not after a write.
#
# Why: some UHZ68LV firmware crashes its internal ProjectorService under our
# read traffic (the on-screen "ProjectorService: Central service has been
# disconnected" toast, roughly hourly). The fully-disabled baseline (every
# group False) ran stable for days, which confirmed the *reads* are the
# trigger. Re-enable ONE group at a time (reload the integration after each
# change) to find which read the firmware chokes on. See ``DIAGNOSTICS.md`` and
# GitHub issue #1.
#
# Notes:
#   * If every group is False, nothing is polled at all -- the connection still
#     opens so commands and the projector's unsolicited status pushes work.
#   * Which entity keys belong to each group is defined in ``POLL_GROUP_KEYS``
#     below. A readable entity whose key is not listed there (e.g. from another
#     projector profile) is polled normally, so this only gates the UHZ68LV.
POLL_GROUPS = {
    "power":       True,   # 124/1 power state
    "source":      False,  # 121/1 input source
    "picture":     False,  # 123/1 mode, 125/1 brightness, 126/1 contrast, 127/1 aspect
    "signal":      False,  # 150/4 resolution, 150/19 refresh rate
    "av":          False,  # 355/1 AV mute, 356/1 audio mute
    "laser":       False,  # 108/1 light source hours
    "temperature": False,  # 150/18 system temp, 155/1 temp status
    "device_info": False,  # 122/1 firmware, 555/1 MAC, 87/3 IP, 353/1 serial, 558/1 id
}

# Entity ``key`` -> poll group, for the UHZ68LV profile. Read-back-less controls
# (3D, sharpness, light-source power, ...) are omitted because they are never
# polled anyway. Keep in sync with the profile if new readable entities appear.
POLL_GROUP_KEYS = {
    "power": {"power"},
    "source": {"input_source"},
    "picture": {"picture_mode", "brightness", "contrast", "aspect_ratio"},
    "signal": {"resolution", "refresh_rate"},
    "av": {"av_mute", "audio_mute"},
    "laser": {"lamp_hours"},
    "temperature": {"system_temperature", "temperature_status"},
    "device_info": {
        "firmware_version", "mac_address", "ip_address",
        "serial_number", "projector_id",
    },
}

# Reverse lookup built once at import.
_KEY_TO_POLL_GROUP = {
    key: group for group, keys in POLL_GROUP_KEYS.items() for key in keys
}

# True if any group is enabled. When False the integration polls nothing.
POLLING_ENABLED = any(POLL_GROUPS.values())


def is_key_polled(key: str) -> bool:
    """Whether the entity with this key should be polled in the diagnostic build.

    A key mapped to a group is polled only if that group is enabled. A key not
    in any group (e.g. another projector profile) is polled normally.
    """
    group = _KEY_TO_POLL_GROUP.get(key)
    if group is None:
        return True
    return POLL_GROUPS.get(group, False)


# --- Diagnostic build: burst-size probe ------------------------------------
# Inflate each poll cycle to this many reads by repeating a known-safe read
# (power, 124/1) after the real ones -- WITHOUT adding any new distinct read.
# This isolates burst SIZE from burst CONTENT: e.g. run "power group only" with
# POLL_PAD_TO = 10 to fire ten power reads per cycle.
#
#   * Crashes  -> the trigger is burst / queue depth (too many queries in one
#     tight cycle, regardless of what they are). Fix = cap/stagger reads/cycle.
#   * Stays clean -> burst count alone is not it; the crash is content-specific
#     (a poison read) -- re-enable a group and sub-bisect.
#
# Caveat: N identical reads may not perfectly proxy N *distinct* reads if the
# firmware handles repeated queries differently. Set 0 to disable padding.
POLL_PAD_TO = 10

# The read repeated for padding (a known-safe query). Kept here so it is easy to
# change if power ever turns out to be the poison read.
POLL_PAD_READ = ("124", "1")

# --- Config entry keys -----------------------------------------------------
CONF_CONNECTION_TYPE = "connection_type"
CONF_PROJECTOR_ID = "projector_id"
CONF_PASSWORD = "password"
CONF_MODEL = "model"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SERIAL_PORT = "serial_port"
CONF_BAUD_RATE = "baud_rate"

CONNECTION_TYPE_LAN = "lan"
CONNECTION_TYPE_SERIAL = "serial"

DEFAULT_PORT = 23
DEFAULT_PROJECTOR_ID = "00"
DEFAULT_NAME = "Optoma Projector"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 300

# 9600 8-N-1 is the de-facto standard across Optoma's RS232 documentation;
# none of the three reference docs behind the bundled profiles state a
# different rate. Exposed as an editable field (not hardcoded) since some
# models/firmwares are known to differ -- check your projector's manual if
# the serial connection doesn't respond.
DEFAULT_BAUD_RATE = 9600
BAUD_RATE_OPTIONS = [9600, 19200, 38400, 57600, 115200]

CONNECT_TIMEOUT = 5
COMMAND_TIMEOUT = 5

# Terminator used by the projector's ASCII protocol.
TERMINATOR = "\r"

# Response prefixes/markers.
RESPONSE_OK_PREFIX = "Ok"
RESPONSE_PASS = "P"
RESPONSE_FAIL = "F"

# Read command used for model auto-detection during config flow, independent
# of which profile ends up matching. Sub-value 3 is the "Regulatory Model
# Name", which returns a stable identifying string (e.g. "VDUHZLBLV" for the
# UHZ68LV) rather than the small, ambiguous numeric index sub-value 1 returns.
MODEL_NAME_READ = ("151", "3")

# Read command for Standby Power Mode (0 = Eco, 1 = Active, 2/3 = Communication).
# Used during setup to warn when a projector left in Eco standby will stop
# answering network commands after a while powered off.
STANDBY_MODE_READ = ("150", "16")

# --- "System Auto Send" status codes ---------------------------------------
# The projector pushes these unsolicited as ``INFOn`` lines on power and fault
# transitions. AUTO_SEND_OPERATIONAL maps the running states to a top-level
# status string; every code in AUTO_SEND_FAULTS becomes status "error".
AUTO_SEND_OPERATIONAL = {
    0: "standby",
    1: "warming_up",
    2: "cooling_down",
    24: "on",
}
AUTO_SEND_FAULTS = {
    4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23, 26, 27
}
# Human-readable label for each documented code (surfaced as an attribute).
AUTO_SEND_MESSAGES = {
    0: "Standby",
    1: "Warming up",
    2: "Cooling down",
    3: "Signal out of range",
    4: "Lamp/LED fail",
    5: "Thermal switch error",
    6: "Fan lock",
    7: "Over temperature",
    8: "Light source hours running out",
    9: "Cover open",
    10: "Lamp ignite fail",
    11: "Format board power-on fail",
    12: "Color wheel unexpected stop",
    14: "Fan 1 lock",
    15: "Fan 2 lock",
    16: "Fan 3 lock",
    17: "Fan 4 lock",
    18: "Fan 5 lock",
    19: "LAN fail, restarting",
    20: "Light source below 60%",
    21: "LD NTC 1 over temperature",
    22: "LD NTC 2 over temperature",
    23: "High ambient temperature",
    24: "System ready",
    26: "Fan 6 lock",
    27: "Fan 7 lock",
}
# Internal status -> display label for the Status sensor.
STATUS_LABELS = {
    "standby": "Off",
    "warming_up": "Warming up",
    "on": "On",
    "cooling_down": "Cooling down",
    "error": "Error",
}
STATUS_OPTIONS = ["Off", "Warming up", "On", "Cooling down", "Error"]
