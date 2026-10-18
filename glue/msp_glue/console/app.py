"""MSP Console web application (FastAPI).

From kit 1.2.0 the pages are a React application (glue/console-ui, built into console/ui), served on
the same addresses as before (/ops, /customers/<code>, /service, /audit ...), so links in chat alerts
and bookmarks keep working. The pages read and change everything through the JSON interface in
api.py (/api/v1). Sign-in stays here: Keycloak, OpenID Connect with PKCE, a server-side session.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from ..settings import settings
from . import actions, api, auth, store

log = logging.getLogger("msp_glue.console")
HERE = Path(__file__).resolve().parent
UI = HERE / "ui"
VERSION = "1.2"
BRANDING = Path(os.environ.get("CONSOLE_BRANDING_DIR", "/branding"))
# Page addresses the React application answers (react-router routes in console-ui/src/routes.tsx).
PAGES = ("/", "/ops", "/customers", "/changes", "/service", "/audit", "/tools")


def _session_secret() -> str:
    if settings.console_session_secret:
        return settings.console_session_secret
    import secrets
    log.warning("CONSOLE_SESSION_SECRET is empty: sessions end whenever the console restarts")
    return secrets.token_urlsafe(32)


@asynccontextmanager
async def lifespan(_app):
    actions.resume()   # follow-ups that were running when the console stopped
    yield


app = FastAPI(title="MSP Console", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_session_secret(), session_cookie="msp_console",
                   max_age=auth.SESSION_SECONDS, same_site="lax",
                   https_only=settings.public_url("console").startswith("https://"))
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
if (UI / "assets").is_dir():
    app.mount("/ui/assets", StaticFiles(directory=UI / "assets"), name="ui-assets")
app.include_router(api.router)


def csp() -> str:
    sso = settings.public_url("keycloak")
    return ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
            f"frame-ancestors 'none'; base-uri 'none'; form-action 'self' {sso}")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers.setdefault("Content-Security-Policy", csp())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if path.startswith("/ui/assets/"):
        # File names carry a hash of their content, so a new build always has new names.
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif path not in ("/healthz",) and not path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# =============================================================================
# Message pages (sign-in problems, a page without the built front end)
# =============================================================================
env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                  trim_blocks=True, lstrip_blocks=True)


def message(title: str, text: str, status: int = 200, retry: str = "/", retry_label: str = "Try again") -> HTMLResponse:
    body = env.get_template("message.html").render(title=title, message=text, retry=retry, retry_label=retry_label,
                                                   version=VERSION)
    return HTMLResponse(body, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    titles = {401: "Signed out", 403: "Not allowed", 404: "Not found"}
    return message(titles.get(exc.status_code, "Something went wrong"), str(exc.detail), status=exc.status_code,
                   retry="/", retry_label="Back to the console")


# =============================================================================
# Sign-in
# =============================================================================
@app.get("/auth/login")
async def login(request: Request, next: str = "/"):  # noqa: A002 - query parameter name
    request.session["next"] = auth.safe_next(next)
    try:
        return await auth.oauth_client().authorize_redirect(request, f"{auth.console_url()}/auth/callback")
    except Exception as exc:  # noqa: BLE001
        log.warning("Console: Keycloak not reachable for sign-in: %s", exc)
        return message("Sign-in service not reachable", "The console could not reach Keycloak (sso). Try again in a "
                       "minute; if it stays like this, check the keycloak container.", status=503, retry="/auth/login")


@app.get("/auth/callback")
async def callback(request: Request):
    from authlib.integrations.base_client.errors import OAuthError
    try:
        token = await auth.oauth_client().authorize_access_token(request)
    except OAuthError as exc:
        log.info("Console: sign-in did not complete: %s", exc)
        return message("Sign-in did not complete", f"Keycloak answered: {exc.description or exc.error}. Start again.",
                       status=400, retry="/auth/login")
    claims = token.get("userinfo") or {}
    user = auth.user_from_claims(claims)
    address = auth.client_address(request)
    if not user.roles:
        store.record(user.as_dict(), "sign-in refused", reason="no MSP Console role", outcome="refused",
                     address=address)
        request.session.clear()
        url = auth.logout_url(token.get("id_token", ""))
        return message("No access to the console",
                       f"The account {user.email} has no MSP Console role. An administrator gives one with "
                       f"\"mspctl staff add {user.email} --role engineer\" (or manager, automation).", status=403,
                       retry=url, retry_label="Sign out of Keycloak")
    next_page = request.session.pop("next", "/")
    auth.start_session(request, user, token)
    store.record(user.as_dict(), "sign-in", reason=", ".join(user.role_names), outcome="done", address=address)
    return RedirectResponse(auth.safe_next(next_page), status_code=303)


@app.post("/auth/logout")
def logout(request: Request, csrf: str = Form("")):
    user = auth.current_user(request)
    if user:
        auth.check_form(request, csrf)
        store.record(user.as_dict(), "sign-out", outcome="done", address=auth.client_address(request))
    return RedirectResponse(auth.end_session(request), status_code=303)


@app.get("/auth/signed-out")
def signed_out(request: Request):
    return message("Signed out", "You are signed out of the MSP Console and of Keycloak.", retry="/",
                   retry_label="Sign in again")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": VERSION, "ui": (UI / "index.html").is_file()}


# =============================================================================
# The operator's logo (branding/logo.svg or logo.png, mounted at CONSOLE_BRANDING_DIR)
# =============================================================================
@app.get("/ui/brand/logo")
def brand_logo():
    for name, media in (("logo.svg", "image/svg+xml"), ("logo.png", "image/png")):
        path = BRANDING / name
        if path.is_file():
            return FileResponse(path, media_type=media, headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "Cache-Control": "no-cache"})
    # No logo: "no content", which the page reads as "show the neutral mark" without an error in the browser
    return Response(status_code=204, headers={"Cache-Control": "no-cache"})


# =============================================================================
# Exports, as linked from 1.1 pages and bookmarks
# =============================================================================
@app.get("/service.csv")
def service_csv(request: Request):
    return RedirectResponse("/api/v1/service.csv" + (f"?{request.url.query}" if request.url.query else ""), 307)


@app.get("/audit.csv")
def audit_csv(request: Request):
    return RedirectResponse("/api/v1/audit.csv" + (f"?{request.url.query}" if request.url.query else ""), 307)


# =============================================================================
# The pages: one React application for every page address
# =============================================================================
def _page(request: Request):
    if not auth.current_user(request):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/auth/login?next={quote(target)}", status_code=303)
    index = UI / "index.html"
    if not index.is_file():
        return message("Console pages not built", "This console image has no built pages (msp_glue/console/ui). "
                       "Build the msp-glue image again: docker compose build msp-glue.", status=503)
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})


for _path in PAGES:
    app.add_api_route(_path, _page, methods=["GET"], include_in_schema=False)
app.add_api_route("/customers/{rest:path}", _page, methods=["GET"], include_in_schema=False)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str, request: Request):
    return api.not_found(request)
