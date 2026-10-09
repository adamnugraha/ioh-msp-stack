"""Nautobot configuration for the IOH managed services platform.

Most core settings (database, cache, allowed hosts, secret key) are read from
NAUTOBOT_* environment variables by nautobot.core.settings, so this file only
holds what is specific to the platform: apps, Golden Config and Nornir.
"""

import os

from nautobot.core.settings import *  # noqa: F401,F403
from nautobot.core.settings_funcs import is_truthy

# A device name only has to be unique within one customer and location.
# Two customers can both have a router called "core-01".
DEVICE_UNIQUENESS = os.getenv("NAUTOBOT_DEVICE_UNIQUENESS", "location_tenant_name")

# Keep one year of change history (the default is 90 days).
CHANGELOG_RETENTION = int(os.getenv("NAUTOBOT_CHANGELOG_RETENTION", "365"))

# Job results older than this are cleaned by the built-in cleanup job.
METRICS_ENABLED = is_truthy(os.getenv("NAUTOBOT_METRICS_ENABLED", "True"))

PLUGINS = [
    "nautobot_plugin_nornir",
    "nautobot_golden_config",
]

PLUGINS_CONFIG = {
    "nautobot_plugin_nornir": {
        # Device logins come from the Secrets Group assigned to each device.
        # msp-glue assigns the customer's Secrets Group automatically.
        "nornir_settings": {
            "credentials": "nautobot_plugin_nornir.plugins.credentials.nautobot_secrets.CredentialsNautobotSecrets",
            "runner": {
                "plugin": "threaded",
                "options": {"num_workers": int(os.getenv("NORNIR_WORKERS", "20"))},
            },
        },
        # Allow per-customer config contexts to change the connection options,
        # for example to reach a customer network through a jump host.
        "use_config_context": {"secrets": False, "connection_options": True},
        "connection_options": {
            "netmiko": {"extras": {"global_delay_factor": 1, "fast_cli": False}},
        },
    },
    "nautobot_golden_config": {
        "per_feature_bar_width": 0.15,
        "per_feature_width": 13,
        "per_feature_height": 4,
        "enable_backup": True,
        "enable_compliance": True,
        "enable_intended": True,
        "enable_sotagg": True,
        "enable_plan": True,
        "enable_deploy": True,
        "enable_postprocessing": False,
        "sot_agg_transposer": None,
        "jinja_env": {
            "undefined": "jinja2.StrictUndefined",
            "trim_blocks": True,
            "lstrip_blocks": True,
        },
    },
}
