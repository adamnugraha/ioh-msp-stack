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

# ------------------------------------------------------------------------------------------------
# Single sign-on through Keycloak, once "./scripts/install.sh sso-tools nautobot" has set NAUTOBOT_SSO=true
# and the values below. Staff then choose the single sign-on button on the log-in page; the local log-in
# stays for the admin account and the API token. The Keycloak groups "MSP managers", "MSP engineers" and
# "MSP automation" become the Nautobot groups of the same names at every sign-in, with the permissions
# msp-glue gave those groups ("mspctl sso tools nautobot"): managers view, engineers also change the inventory.
if is_truthy(os.getenv("NAUTOBOT_SSO", "False")):
    _realm = f"https://sso.{os.environ['MSP_DOMAIN']}/realms/msp"
    AUTHENTICATION_BACKENDS = [
        "social_core.backends.keycloak.KeycloakOAuth2",
        "nautobot.core.authentication.ObjectPermissionBackend",
    ]
    SOCIAL_AUTH_KEYCLOAK_KEY = "nautobot"
    SOCIAL_AUTH_KEYCLOAK_SECRET = os.environ["NAUTOBOT_SSO_CLIENT_SECRET"]
    SOCIAL_AUTH_KEYCLOAK_PUBLIC_KEY = os.environ["NAUTOBOT_SSO_PUBLIC_KEY"]
    # The browser goes to Keycloak's public address; Nautobot itself asks Keycloak on the platform network.
    SOCIAL_AUTH_KEYCLOAK_AUTHORIZATION_URL = f"{_realm}/protocol/openid-connect/auth"
    SOCIAL_AUTH_KEYCLOAK_ACCESS_TOKEN_URL = "http://keycloak:8080/realms/msp/protocol/openid-connect/token"
    SOCIAL_AUTH_KEYCLOAK_ID_KEY = "email"
    # Caddy ends TLS in front of Nautobot: the address Keycloak sends the browser back to is https.
    SOCIAL_AUTH_REDIRECT_IS_HTTPS = True
    SOCIAL_AUTH_PIPELINE = (
        "social_core.pipeline.social_auth.social_details",
        "social_core.pipeline.social_auth.social_uid",
        "social_core.pipeline.social_auth.auth_allowed",
        "social_core.pipeline.social_auth.social_user",
        "social_core.pipeline.user.get_username",
        "social_core.pipeline.user.create_user",
        "social_core.pipeline.social_auth.associate_user",
        "social_core.pipeline.social_auth.load_extra_data",
        "social_core.pipeline.user.user_details",
        "nautobot.extras.group_sync.group_sync",
    )
    SSO_ENABLE_GROUP_SYNC = True
    SSO_CLAIMS_GROUP = "groups"
    SSO_SYNC_GROUPS = ["MSP managers", "MSP engineers", "MSP automation"]
