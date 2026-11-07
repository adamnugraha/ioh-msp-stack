#!/usr/bin/env python3
"""Create or repair the Keycloak client of the sign-in gate, msp-gate, in the realm msp.

./scripts/install.sh gate runs it on the platform network, with python from the msp-glue image and
these values in its environment:
    KEYCLOAK_ADMIN_PASSWORD        Keycloak's bootstrap administrator (admin)
    KEYCLOAK_GATE_CLIENT_SECRET    the client's secret, the same one the gate (oauth2-proxy) uses
    MSP_DOMAIN                     every gated tool is <name>.<MSP_DOMAIN>
    KEYCLOAK_URL                   optional, default http://keycloak:8080
Standard library only. Safe to run again: the client then follows this script.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REALM = "msp"
CLIENT_ID = "msp-gate"
# The names with the gate in front (components/proxy/Caddyfile): its sign-in comes back to each of them.
GATED = ("console", "nautobot", "zabbix", "semaphore", "git", "glue", "glpi", "sso")
ROLE_CLAIM = {
    "name": "realm roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
    "config": {"multivalued": "true", "claim.name": "roles", "jsonType.label": "String",
               "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"},
}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # inside the platform: never a web proxy


class Failed(Exception):
    pass


def call(method, url, token=None, body=None, form=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raise Failed(f"{method} {url.split('/admin/')[-1]}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
    except (urllib.error.URLError, OSError) as e:
        raise Failed(f"Keycloak does not answer at {url.split('/realms/')[0].split('/admin/')[0]}: {e}")
    return json.loads(raw) if raw.strip() else None


def main():
    base = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080").rstrip("/")
    password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")
    secret = os.environ.get("KEYCLOAK_GATE_CLIENT_SECRET", "")
    domain = os.environ.get("MSP_DOMAIN", "")
    if not password or not secret or not domain:
        raise Failed("KEYCLOAK_ADMIN_PASSWORD, KEYCLOAK_GATE_CLIENT_SECRET and MSP_DOMAIN must be set")
    token = call("POST", f"{base}/realms/master/protocol/openid-connect/token",
                 form={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": password})
    token = token["access_token"]
    admin = f"{base}/admin/realms/{REALM}"
    call("GET", admin, token)  # the realm exists (./scripts/install.sh sso made it)

    redirects = [f"https://{name}.{domain}/oauth2/callback" for name in GATED]
    wanted = {
        "clientId": CLIENT_ID, "name": "Sign-in gate (direct access)", "enabled": True,
        "protocol": "openid-connect", "publicClient": False, "clientAuthenticatorType": "client-secret",
        "secret": secret, "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False, "frontchannelLogout": False,
        "redirectUris": redirects, "webOrigins": [], "rootUrl": f"https://console.{domain}", "baseUrl": "/",
        "attributes": {"pkce.code.challenge.method": "S256"},
    }
    found = call("GET", f"{admin}/clients?clientId={CLIENT_ID}", token) or []
    if found:
        current = found[0]
        attributes = {**current.get("attributes", {}), **wanted["attributes"]}
        call("PUT", f"{admin}/clients/{current['id']}", token, {**current, **wanted, "attributes": attributes,
                                                                "id": current["id"]})
        uuid = current["id"]
        print(f"client {CLIENT_ID} updated")
    else:
        call("POST", f"{admin}/clients", token, wanted)
        uuid = call("GET", f"{admin}/clients?clientId={CLIENT_ID}", token)[0]["id"]
        print(f"client {CLIENT_ID} created")

    mappers = call("GET", f"{admin}/clients/{uuid}/protocol-mappers/models", token) or []
    same = [m for m in mappers if m["name"] == ROLE_CLAIM["name"]]
    if same:
        call("PUT", f"{admin}/clients/{uuid}/protocol-mappers/models/{same[0]['id']}", token,
             {**same[0], **ROLE_CLAIM, "id": same[0]["id"]})
    else:
        call("POST", f"{admin}/clients/{uuid}/protocol-mappers/models", token, ROLE_CLAIM)
    print(f"roles claim in the client's tokens; sign-in returns to {len(redirects)} names under {domain}")


if __name__ == "__main__":
    try:
        main()
    except Failed as e:
        print(f"FAIL {e}", file=sys.stderr)
        sys.exit(1)
