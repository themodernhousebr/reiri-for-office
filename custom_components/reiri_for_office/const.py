"""Constants for Reiri for Office."""

DOMAIN = "reiri_for_office"
PLATFORMS = ["climate"]

CONF_SLAVE_POLICY = "slave_policy"
SLAVE_POLICY_CONSERVATIVE = "conservative"
SLAVE_POLICY_ALL_REPORTED = "all_reported"
DEFAULT_PORT = 52001
DEFAULT_SLAVE_POLICY = SLAVE_POLICY_CONSERVATIVE

MODE_TO_HA = {"C": "cool", "H": "heat", "D": "dry", "F": "fan_only"}
HA_TO_MODE = {value: key for key, value in MODE_TO_HA.items()}
FLAP_DEBOUNCE_SECONDS = 1.2
