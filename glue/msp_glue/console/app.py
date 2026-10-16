"""MSP Console web application (FastAPI, server-rendered pages).

Pages
  /ops                 Operations: problems, customers, tickets needing attention, integration jobs
  /customers/{code}    One customer: devices, problems, tickets, automation, recent actions
  /service             Service overview for managers: availability against target, service levels, compliance
  /audit               Every change made from the console, and who is signed in
Actions (POST, engineer role; playbooks also need the automation role)
  /actions/ack  /actions/resync  /actions/config-check  /actions/playbook
"""

from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

from ..settings import settings
from . import actions, auth, data, store

log = logging.getLogger("msp_glue.console")
HERE = Path(__file__).resolve().parent
VERSION = "1.1"
REFRESH_SECONDS = 60

SOURCES = {
    "customers": "Nautobot customers", "devices": "Nautobot devices", "hosts": "Zabbix hosts",
    "problems": "Zabbix problems", "open_tickets": "GLPI tickets", "month_tickets": "GLPI ticket statistics",
    "availability": "Zabbix availability", "compliance": "Golden Config compliance",
    "backups": "Golden Config backups", "last_runs": "Semaphore tasks", "glue_jobs": "msp-glue jobs",
}


class NeedLogin(Exception):
    pass


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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    sso = settings.public_url("keycloak")
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                f"frame-ancestors 'none'; base-uri 'none'; form-action 'self' {sso}")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path not in ("/healthz",) and not request.url.path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# =============================================================================
# Templates
# =============================================================================
env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                  trim_blocks=True, lstrip_blocks=True)


def _when(value: datetime | None, full: bool = False) -> str:
    if not value:
        return ""
    value = value.astimezone(data.tz())
    if full:
        return value.strftime("%d %b %Y %H:%M:%S")
    if value.date() == data.now().date():
        return value.strftime("%H:%M")
    return value.strftime("%d %b %H:%M")


def _pct(value, digits: int = 2) -> str:
    return "" if value is None else f"{value:.{digits}f} %"


def _hours(value) -> str:
    if value is None:
        return ""
    if value < 1:
        return f"{max(1, round(value * 60))} min"
    return f"{value:.1f} h" if value < 48 else f"{value / 24:.1f} days"


def _seconds(value) -> str:
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    return sign + data.duration(abs(value)) if abs(value) >= 60 else f"{sign}{abs(value)} s"


def status_class(value) -> str:
    """Colour of a status pill: ok, bad, warn, info or muted."""
    value = str(value or "").lower()
    if value in ("success", "done", "ok", "provisioned", "up", "met"):
        return "ok"
    if value in ("error", "failed", "failure", "down", "refused", "breached"):
        return "bad"
    if value in ("stopped", "unknown", "pending", "stopping", "errors"):
        return "warn"
    if value in ("running", "waiting", "starting", "queued", "started", "confirmed"):
        return "info"
    return "muted"


def _plural(count, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


env.filters.update(ago=data.ago, when=_when, pct=_pct, hours=_hours, seconds=_seconds, plural=_plural)
env.globals.update(SEVERITIES=data.SEVERITIES, version=VERSION, status_class=status_class)


def tool_links() -> dict:
    links = {
        "Nautobot": settings.public_url("nautobot"), "Zabbix": settings.public_url("zabbix"),
        "GLPI": settings.public_url("glpi"), "Semaphore": settings.public_url("semaphore"),
        "Forgejo": settings.public_url("forgejo"),
    }
    if settings.grafana_url:
        links["Grafana"] = settings.public_url("grafana")
    return links


def _errors() -> list[dict]:
    out = []
    for key, error in data.errors().items():
        base = key.split(":")[0]
        name = SOURCES.get(base, "Semaphore project" if base == "automation" else key)
        loaded = data.cache.loaded(key)
        out.append({"source": name, "error": error, "since": data.ago(loaded) if loaded else None})
    return out


def render(request: Request, template: str, user: auth.User | None, status: int = 200, chrome: bool = True,
           **context) -> HTMLResponse:
    """Render a page. chrome=False skips the tool health strip (for pages rendered on the event loop,
    which must not wait for the tools)."""
    refresh_call = request.headers.get("x-console-refresh") == "1"
    flash = None if refresh_call else request.session.pop("flash", None)
    chrome = chrome and user is not None
    body = env.get_template(template).render(
        request=request, user=user, csrf=auth.csrf_token(request) if user else "", flash=flash,
        tools=tool_links(), account_url=f"{settings.public_url('keycloak')}/realms/{settings.keycloak_realm}/account",
        health=data.health() if chrome else [], glue=data.glue_jobs() if chrome and user.is_engineer else None,
        source_errors=_errors() if chrome else [], rendered=data.now(), tzname=str(data.tz()),
        refresh=REFRESH_SECONDS, **context)
    return HTMLResponse(body, status_code=status)


def need_user(request: Request) -> auth.User:
    user = auth.current_user(request)
    if not user:
        raise NeedLogin()
    return user


def flash_back(request: Request, kind: str, message: str, back: str) -> RedirectResponse:
    request.session["flash"] = (kind, message)
    return RedirectResponse(auth.safe_next(back), status_code=303)


@app.exception_handler(NeedLogin)
async def _need_login(request: Request, exc: NeedLogin):
    if request.headers.get("x-console-refresh") == "1":
        return JSONResponse({"detail": "signed out"}, status_code=401)
    if request.method != "GET":
        # A form sent after the session ended: nothing was done, and the person needs to know that.
        return render(request, "message.html", None, status=401, title="Signed out",
                      message="Your session ended before this was sent, so nothing was done. Sign in again and "
                              "repeat it.", retry="/auth/login", retry_label="Sign in again")
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/auth/login?next={quote(target)}", status_code=303)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if request.headers.get("x-console-refresh") == "1":
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    titles = {403: "Not allowed", 404: "Not found"}
    user = auth.current_user(request) if request.session.get("sid") else None
    return render(request, "message.html", user, status=exc.status_code, chrome=False,
                  title=titles.get(exc.status_code, "Something went wrong"), message=str(exc.detail), retry="/",
                  retry_label="Back to the console")


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
        return render(request, "message.html", None, status=503, title="Sign-in service not reachable",
                      message="The console could not reach Keycloak (sso). Try again in a minute; if it stays like "
                              "this, check the keycloak container.", retry="/auth/login")


@app.get("/auth/callback")
async def callback(request: Request):
    from authlib.integrations.base_client.errors import OAuthError
    try:
        token = await auth.oauth_client().authorize_access_token(request)
    except OAuthError as exc:
        log.info("Console: sign-in did not complete: %s", exc)
        return render(request, "message.html", None, status=400, title="Sign-in did not complete",
                      message=f"Keycloak answered: {exc.description or exc.error}. Start again.", retry="/auth/login")
    claims = token.get("userinfo") or {}
    user = auth.user_from_claims(claims)
    address = auth.client_address(request)
    if not user.roles:
        store.record(user.as_dict(), "sign-in refused", reason="no MSP Console role", outcome="refused",
                     address=address)
        request.session.clear()
        url = auth.logout_url(token.get("id_token", ""))
        return render(request, "message.html", None, status=403, title="No access to the console",
                      message=f"The account {user.email} has no MSP Console role. An administrator gives one with "
                              f"\"mspctl staff add {user.email} --role engineer\" (or manager, automation).",
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
    return render(request, "message.html", None, title="Signed out", message="You are signed out of the MSP Console "
                  "and of Keycloak.", retry="/", retry_label="Sign in again")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": VERSION}


# =============================================================================
# Pages
# =============================================================================
@app.get("/")
def home(request: Request):
    user = need_user(request)
    return RedirectResponse("/ops" if user.is_engineer or not user.is_manager else "/service", status_code=303)


@app.get("/ops")
def ops(request: Request):
    user = need_user(request)
    data.prefetch(data.health, data.customers, data.devices, data.hosts, data.problems, data.open_tickets,
                  data.compliance, data.backups, data.last_runs, data.glue_jobs)
    rows = data.customer_rows()
    problems = data.problems()
    tickets = data.open_tickets()
    tiles = {
        "problems": len(problems),
        "by_severity": {s: sum(1 for p in problems if p["severity"] == s) for s in (5, 4, 3, 2)},
        "unacknowledged": sum(1 for p in problems if not p["acknowledged"]),
        "down": sum(r["down"] for r in rows), "unknown": sum(r["unknown"] for r in rows),
        "unmonitored": sum(r["unmonitored"] for r in rows),
        "tickets": len(tickets), "late": sum(1 for t in tickets if t["late"]),
        "customers": len(rows), "setup_errors": sum(1 for r in rows if r["provisioning"] == "error"),
    }
    return render(request, "ops.html", user, page="ops", rows=rows, problems=problems, tickets=tickets[:40],
                  tiles=tiles, customers={r["code"]: r["name"] for r in rows})


@app.get("/customers")
def customers_index(request: Request):
    need_user(request)
    return RedirectResponse("/ops#customers", status_code=303)


@app.get("/customers/{code}")
def customer_page(code: str, request: Request):
    user = need_user(request)
    tenant = data.customer(code)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"No managed customer with code {code}.")
    data.prefetch(data.health, data.devices, data.hosts, data.problems, data.open_tickets, data.compliance,
                  data.backups, data.last_runs, data.availability, data.month_tickets,
                  lambda: data.automation(code), lambda: data.grafana_org(tenant))
    hosts, comp, backs = data.hosts(), data.compliance(), data.backups()
    devices = []
    for d in sorted((d for d in data.devices() if d["customer"] == code),
                    key=lambda d: (d["status"] != "Active", d["site"], d["name"])):
        devices.append({**d, "host": hosts.get(d["id"]), "check": comp.get(d["id"]), "backup": backs.get(d["id"])})
    summary = next((r for r in data.customer_rows() if r["code"] == code), {})
    service = next((r for r in data.service_rows() if r["code"] == code), {})
    automation = data.automation(code)
    links = _customer_links(tenant)
    org = data.grafana_org(tenant)
    if org:
        links["Grafana dashboards"] = f"{settings.public_url('grafana')}/dashboards?orgId={org}"
    return render(request, "customer.html", user, page="customer", tenant=tenant, devices=devices,
                  summary=summary, service=service, automation=automation, links=links,
                  problems=[p for p in data.problems() if p["customer"] == code],
                  tickets=[t for t in data.open_tickets() if t["customer"] == code],
                  audit=store.entries(limit=15, customer=code), change_only=actions.change_only_templates(),
                  device_names=sorted(d["name"] for d in devices if d["status"] == "Active"))


def _customer_links(tenant) -> dict:
    nautobot, zabbix, glpi = settings.public_url("nautobot"), settings.public_url("zabbix"), settings.public_url("glpi")
    links = {"Nautobot tenant": f"{nautobot}/tenancy/tenants/{tenant.id}/"}
    if tenant.zabbix_hostgroup_id:
        links["Zabbix problems"] = (f"{zabbix}/zabbix.php?action=problem.view&filter_set=1"
                                    f"&groupids%5B%5D={tenant.zabbix_hostgroup_id}")
    if tenant.glpi_entity_id:
        links["GLPI tickets"] = (f"{glpi}/front/ticket.php?criteria%5B0%5D%5Bfield%5D=80&criteria%5B0%5D%5Bsearchtype%5D"
                                 f"=equals&criteria%5B0%5D%5Bvalue%5D={tenant.glpi_entity_id}")
    if tenant.semaphore_project_id:
        links["Semaphore tasks"] = f"{settings.public_url('semaphore')}/project/{tenant.semaphore_project_id}/history"
    prefix = settings.mappings.get("golden_config", {}).get("backup_repo_prefix", "backups-")
    links["Configuration backups"] = f"{settings.public_url('forgejo')}/{settings.forgejo_org}/{prefix}{tenant.code}"
    return links


@app.get("/service")
def service(request: Request):
    user = need_user(request)
    data.prefetch(data.health, data.customers, data.availability, data.month_tickets, data.devices,
                  data.compliance, data.backups, data.open_tickets)
    rows = data.service_rows()
    solved = sum(r["solved"] for r in rows if r["mttr"] is not None)
    totals = {
        "customers": len(rows), "with_sla": sum(1 for r in rows if r["met"] is not None),
        "met": sum(1 for r in rows if r["met"]), "breached": [r for r in rows if r["met"] is False],
        "opened": sum(r["opened"] for r in rows), "solved": sum(r["solved"] for r in rows),
        "late": sum(r["month_late"] for r in rows), "open": sum(r["open"] for r in rows),
        "open_late": sum(r["open_late"] for r in rows),
        "mttr": round(sum(r["mttr"] * r["solved"] for r in rows if r["mttr"] is not None) / solved, 1) if solved else None,
        "compliance": _ratio(sum(r["compliant_n"] for r in rows), sum(r["checked_n"] for r in rows)),
        "backup": _ratio(sum(r["fresh_n"] for r in rows), sum(r["net_n"] for r in rows)),
    }
    start = data.month_start()
    return render(request, "service.html", user, page="service", rows=rows, totals=totals,
                  month=start.strftime("%B %Y"), period_from=start)


def _ratio(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


@app.get("/service.csv")
def service_csv(request: Request):
    need_user(request)
    rows = data.service_rows()
    columns = ["code", "name", "tier", "regulated", "slo", "sli", "previous", "downtime", "met", "opened", "solved",
               "month_late", "own_late", "resolve_late", "mttr", "open", "open_late", "compliance", "backup", "active"]
    return _csv(f"service-{data.now():%Y-%m}.csv", columns, rows)


@app.get("/audit")
def audit(request: Request, customer: str = "", email: str = "", action: str = ""):
    user = need_user(request)
    return render(request, "audit.html", user, page="audit",
                  entries=store.entries(limit=500, customer=customer, email=email, action=action),
                  filters={"customer": customer, "email": email, "action": action},
                  choices={c: store.distinct(c) for c in ("customer", "email", "action")},
                  sessions=store.active_sessions())


@app.get("/audit.csv")
def audit_csv(request: Request, customer: str = "", email: str = "", action: str = ""):
    need_user(request)
    rows = store.entries(limit=100000, customer=customer, email=email, action=action)
    for r in rows:
        r["at"] = r["at"].isoformat(timespec="seconds")
        r["updated"] = r["updated"].isoformat(timespec="seconds") if r["updated"] else ""
    columns = ["id", "at", "email", "name", "action", "customer", "target", "ticket", "reason", "outcome", "result",
               "address", "updated"]
    return _csv(f"console-audit-{data.now():%Y%m%d}.csv", columns, rows)


def _csv(filename: str, columns: list[str], rows: list[dict]) -> Response:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # A leading =, +, - or @ makes spreadsheet programs run the cell as a formula.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()})
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# =============================================================================
# Actions
# =============================================================================
def _act(request: Request, csrf: str, back: str, func, *args) -> RedirectResponse:
    user = need_user(request)
    auth.check_form(request, csrf)
    try:
        message = func(user, *args, address=auth.client_address(request))
        return flash_back(request, "ok", message, back)
    except actions.ActionError as exc:
        return flash_back(request, "error", str(exc), back)
    except Exception as exc:  # noqa: BLE001
        log.exception("Console action failed")
        return flash_back(request, "error", f"The action failed: {type(exc).__name__}: {exc}", back)


@app.post("/actions/ack")
def action_ack(request: Request, csrf: str = Form(""), eventid: str = Form(""), note: str = Form(""),
               back: str = Form("/ops")):
    return _act(request, csrf, back, actions.acknowledge, eventid, note)


@app.post("/actions/resync")
def action_resync(request: Request, csrf: str = Form(""), code: str = Form(""), back: str = Form("/ops")):
    return _act(request, csrf, back, actions.resync, code)


@app.post("/actions/config-check")
def action_config_check(request: Request, csrf: str = Form(""), code: str = Form(""), back: str = Form("/ops")):
    return _act(request, csrf, back, actions.config_check, code)


@app.post("/actions/playbook")
def action_playbook(request: Request, csrf: str = Form(""), code: str = Form(""), template_id: str = Form(""),
                    devices: list[str] = Form(default=[]), item_type: str = Form("Ticket"), item_id: str = Form(""),
                    reason: str = Form(""), variables: str = Form(""), back: str = Form("/ops")):
    return _act(request, csrf, back, actions.run_playbook, code, template_id, devices, item_type, item_id, reason,
                variables)
