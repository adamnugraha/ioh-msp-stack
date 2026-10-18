"""Sign-in through Keycloak (OpenID Connect, authorisation code with PKCE) and the console's roles.

The browser only ever holds a signed cookie with a random session id. The tokens stay in the
console's database, and every five minutes the console asks Keycloak whether the session is still
valid: an account disabled with "mspctl staff disable" or a role removed in Keycloak takes effect
within that time.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from ..http import make_session
from ..settings import settings
from . import store

log = logging.getLogger("msp_glue.console.auth")

MANAGER, ENGINEER, AUTOMATION = "msp-manager", "msp-engineer", "msp-automation"
CONSOLE_ROLES = (MANAGER, ENGINEER, AUTOMATION)
CLIENT_ID = "msp-console"
SESSION_SECONDS = 36000      # 10 hours, the realm's longest session (ssoSessionMaxLifespan)
CHECK_SECONDS = 300          # ask Keycloak again after this long


def console_url() -> str:
    return settings.public_url("console")


def discovery_url() -> str:
    return f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}/.well-known/openid-configuration"


_meta: dict = {}


def metadata() -> dict:
    """Keycloak's OpenID configuration. Read over the internal address; the browser-facing endpoints in it
    (authorisation, logout) carry the public sso.<MSP_DOMAIN> name because of KC_HOSTNAME."""
    if not _meta or time.time() - _meta.get("_at", 0) > 3600:
        response = make_session().get(discovery_url(), timeout=10)
        response.raise_for_status()
        _meta.clear()
        _meta.update(response.json(), _at=time.time())
    return _meta


def oauth_client():
    """The Authlib client, created on first use so the console starts even while Keycloak is down."""
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        oauth.register(
            name="keycloak", client_id=CLIENT_ID, client_secret=settings.keycloak_console_secret,
            server_metadata_url=discovery_url(),
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256", "timeout": 15},
        )
        _oauth = oauth.keycloak
    return _oauth


_oauth = None


# =============================================================================
# The signed-in person
# =============================================================================
@dataclass
class User:
    sub: str
    email: str
    name: str
    roles: list[str] = field(default_factory=list)
    sid: str = ""

    @property
    def is_manager(self) -> bool:
        return MANAGER in self.roles

    @property
    def is_engineer(self) -> bool:
        return ENGINEER in self.roles

    @property
    def can_act(self) -> bool:
        """Acknowledge problems, re-sync customers, start configuration checks."""
        return ENGINEER in self.roles

    @property
    def can_run(self) -> bool:
        """Start Semaphore playbooks."""
        return ENGINEER in self.roles and AUTOMATION in self.roles

    @property
    def role_names(self) -> list[str]:
        labels = {MANAGER: "manager", ENGINEER: "engineer", AUTOMATION: "automation"}
        return [labels[r] for r in CONSOLE_ROLES if r in self.roles]

    def as_dict(self) -> dict:
        return {"sub": self.sub, "email": self.email, "name": self.name, "roles": self.roles}


def user_from_claims(claims: dict) -> User:
    email = (claims.get("email") or claims.get("preferred_username") or "").lower()
    name = claims.get("name") or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x)
    roles = sorted(r for r in (claims.get("roles") or []) if r in CONSOLE_ROLES)
    return User(sub=claims.get("sub", ""), email=email, name=(name or email).strip(), roles=roles)


def client_address(request: Request) -> str:
    """For the audit trail: the address Caddy saw and, through a Cloudflare Tunnel, the visitor's address
    that Cloudflare reports in CF-Connecting-IP. A browser on the network could send that header itself,
    so it is recorded next to the address Caddy saw, never instead of it."""
    peer = request.client.host if request.client else ""
    reported = request.headers.get("cf-connecting-ip", "").strip()
    if reported and reported != peer:
        return f"{reported} via {peer}"[:120]
    return peer[:120]


def current_user(request: Request) -> User | None:
    sid = request.session.get("sid")
    if not sid:
        return None
    row = store.load_session(sid)
    if not row:
        request.session.pop("sid", None)
        return None
    if time.time() - row["checked"] > CHECK_SECONDS:
        verdict = _recheck(row)
        if verdict is False:
            store.delete_session(sid)
            request.session.pop("sid", None)
            return None
        if verdict is not None:
            row.update(verdict)
    return User(sub=row["sub"], email=row["email"], name=row["name"], roles=list(row["roles"]), sid=sid)


def _recheck(row: dict) -> dict | None | bool:
    """Refresh the tokens. False: the session is over. None: Keycloak did not answer (keep the session)."""
    try:
        meta = metadata()
        http = make_session(retries=0)
        response = http.post(meta["token_endpoint"], timeout=10, data={
            "grant_type": "refresh_token", "refresh_token": row["refresh"],
            "client_id": CLIENT_ID, "client_secret": settings.keycloak_console_secret})
        if response.status_code in (400, 401):
            log.info("Console session of %s ended in Keycloak (%s)", row["email"], response.text[:200])
            return False
        response.raise_for_status()
        tokens = response.json()
        info = http.get(meta["userinfo_endpoint"], timeout=10,
                        headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if info.status_code == 401:
            return False
        info.raise_for_status()
        user = user_from_claims(info.json())
        if not user.roles:
            log.info("Console: %s no longer has a console role", row["email"])
            return False
        # The Keycloak session slides with every refresh; the console's own session follows it, up to
        # SESSION_SECONDS after the sign-in.
        expires = min(row["created"] + SESSION_SECONDS,
                      time.time() + int(tokens.get("refresh_expires_in") or CHECK_SECONDS) + 60)
        store.refresh_session(row["sid"], user.as_dict(), tokens.get("refresh_token") or row["refresh"],
                              tokens.get("id_token", ""), expires=expires)
        return {"email": user.email, "name": user.name, "roles": user.roles}
    except Exception as exc:  # noqa: BLE001 - Keycloak down: keep working, check again soon
        log.warning("Console: could not check the session of %s with Keycloak: %s", row["email"], exc)
        store.touch_session(row["sid"])   # retry after CHECK_SECONDS rather than on every request
        return None


def start_session(request: Request, user: User, token: dict) -> None:
    sid = secrets.token_urlsafe(32)
    expires = time.time() + min(SESSION_SECONDS, int(token.get("refresh_expires_in") or SESSION_SECONDS) + 60)
    store.save_session(sid, user.as_dict(), token.get("id_token", ""), token.get("refresh_token", ""), expires,
                       address=client_address(request))
    request.session["sid"] = sid
    request.session["csrf"] = secrets.token_urlsafe(24)


def end_session(request: Request) -> str:
    """Forget the session here; returns Keycloak's logout address so the Keycloak session ends too."""
    sid = request.session.get("sid", "")
    row = store.load_session(sid) if sid else None
    if sid:
        store.delete_session(sid)
    request.session.clear()
    return logout_url(row.get("id_token") if row else "")


def logout_url(id_token: str = "") -> str:
    params = {"client_id": CLIENT_ID, "post_logout_redirect_uri": f"{console_url()}/auth/signed-out"}
    if id_token:
        params["id_token_hint"] = id_token   # without it Keycloak asks "Do you want to log out?"
    try:
        endpoint = metadata()["end_session_endpoint"]
    except Exception:  # noqa: BLE001
        endpoint = f"{settings.public_url('keycloak')}/realms/{settings.keycloak_realm}/protocol/openid-connect/logout"
    return f"{endpoint}?{urlencode(params)}"


# =============================================================================
# Forms
# =============================================================================
def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(24)
    return token


def check_form(request: Request, token: str) -> None:
    """Every form carries the session's token, and the browser must say it comes from the console itself."""
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="This form has expired. Reload the page and try again.")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != console_url():
        raise HTTPException(status_code=403, detail=f"Form sent from {origin}, not from the console.")


def safe_next(path: str | None) -> str:
    """Only paths on the console itself, never another site."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/"
