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
