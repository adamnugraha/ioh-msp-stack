"""The console's JSON interface for its pages (/api/v1), as glue/console-ui/src/lib/api/http.ts calls it.

Reads need a signed-in person; every change also needs the session's CSRF token in X-CSRF-Token and,
when the browser sends one, an Origin of the console itself. Each change checks the person's roles
and writes the audit trail before it calls a tool (actions.py).
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import actions, auth, data, detail, investigate, views
from .auth import User

log = logging.getLogger("msp_glue.console.api")
router = APIRouter(prefix="/api/v1")


# =============================================================================== access
def reader(request: Request) -> User:
    user = auth.current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Signed out. Sign in again.")
    return user


def changer(request: Request) -> User:
    user = reader(request)
    expected = request.session.get("csrf", "")
    sent = request.headers.get("x-csrf-token", "")
    if not expected or not hmac.compare_digest(expected, sent):
        raise HTTPException(status_code=403, detail="This page is out of date. Reload it and try again.")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != auth.console_url():
        raise HTTPException(status_code=403, detail=f"Sent from {origin}, not from the console.")
    return user


def act(request: Request, func, *args, **kwargs) -> dict:
    user = changer(request)
    try:
        result = func(user, *args, address=auth.client_address(request), **kwargs)
    except actions.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - say what failed, in the log too
        log.exception("Console action failed")
        raise HTTPException(status_code=502, detail=f"The action failed: {type(exc).__name__}: {exc}") from exc
    return result.as_dict() if isinstance(result, actions.Result) else result


def found(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=what)
    return value


# ================================================================================ reads
@router.get("/me")
def me(request: Request) -> dict:
    return views.me(reader(request), request, investigate.enabled())


@router.get("/health")
def health(request: Request) -> list:
    reader(request)
    return views.health()


@router.get("/overview")
def overview(request: Request) -> dict:
    return views.overview(reader(request))


@router.get("/customers")
def customers(request: Request) -> list:
    reader(request)
    return views.summaries()


@router.get("/devices")
def devices(request: Request) -> list:
    reader(request)
    data.prefetch(data.customers, data.devices, data.hosts)
    return views.device_index()


@router.get("/customers/{code}")
def customer(code: str, request: Request) -> dict:
    reader(request)
    return found(views.customer_detail(code), f"No managed customer with code {code}.")


@router.get("/customers/{code}/topology")
def topology(code: str, request: Request) -> dict:
    reader(request)
    found(data.customer(code), f"No managed customer with code {code}.")
    data.prefetch(data.devices, data.hosts, lambda: data.sites(code))
    return detail.topology(code)


@router.get("/customers/{code}/devices/{name}")
def device(code: str, name: str, request: Request) -> dict:
    reader(request)
    tenant = found(data.customer(code), f"No managed customer with code {code}.")
    return found(views.device_detail(code, name), f"{tenant.name} has no device called {name}.")


@router.get("/devices/{device_id}/metrics")
def metrics(device_id: str, request: Request, range: str = "6h", charts: str = "") -> dict:  # noqa: A002
    reader(request)
    d = found(data.device_by_id(device_id), "Unknown device.")
    wanted = [c for c in charts.split(",") if c][:12]
    return detail.metrics(d, range if range in detail.RANGES else "6h", wanted)


@router.get("/work")
def work(request: Request) -> dict:
    reader(request)
    return views.work()


@router.get("/service")
def service(request: Request, month: str = "") -> dict:
    reader(request)
    return views.service(month or None)


@router.get("/audit")
def audit(request: Request, customer: str = "", email: str = "", action: str = "", outcome: str = "") -> dict:
    reader(request)
    return views.audit(customer, email, action, outcome)


@router.get("/sessions")
def sessions(request: Request) -> list:
    reader(request)
    return views.sessions()


@router.get("/glue")
def glue(request: Request) -> dict:
    reader(request)
    return views.glue()


@router.get("/investigations/{run_id}")
def investigation(run_id: str, request: Request) -> dict:
    reader(request)
    try:
        return investigate.get(run_id)
    except actions.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# ------------------------------------------------------------------------------ exports
def _csv(filename: str, columns: list[str], rows: list[dict]) -> Response:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # A leading =, +, - or @ makes spreadsheet programs run the cell as a formula.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()})
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/service.csv")
def service_csv(request: Request, month: str = "") -> Response:
    reader(request)
    rows = views.service_csv_rows(month or None)
    columns = ["month", "code", "name", "tier", "regulated", "slo", "sli", "downtime_seconds", "met", "opened",
               "solved", "breaches", "own_late", "resolve_late", "mttr_hours", "open", "open_late", "compliance",
               "backup"]
    return _csv(f"service-{rows[0]['month'] if rows else data.now().strftime('%Y-%m')}.csv", columns, rows)


@router.get("/audit.csv")
def audit_csv(request: Request, customer: str = "", email: str = "", action: str = "", outcome: str = "") -> Response:
    reader(request)
    from . import store
    rows = store.entries(limit=100000, customer=customer, email=email, action=action, outcome=outcome)
    for r in rows:
        r["at"] = r["at"].isoformat(timespec="seconds")
        r["updated"] = r["updated"].isoformat(timespec="seconds") if r["updated"] else ""
    columns = ["id", "at", "email", "name", "action", "customer", "target", "ticket", "reason", "outcome", "result",
               "address", "updated"]
    return _csv(f"console-audit-{data.now():%Y%m%d}.csv", columns, rows)


# ============================================================================== changes
class AckBody(BaseModel):
    note: str = ""


class NoteBody(BaseModel):
    itemType: Literal["Ticket", "Change"]
    itemId: int
    text: str


class PlaybookBody(BaseModel):
    customer: str = ""
    templateId: int
    devices: list[str] = Field(default_factory=list)
    itemType: Literal["Ticket", "Change"] = "Ticket"
    itemId: int
    reason: str = ""
    variables: str = ""


class TicketBody(BaseModel):
    customer: str = ""
    kind: Literal["Incident", "Request", "Change"]
    title: str
    description: str
    priority: int = 3
    devices: list[str] = Field(default_factory=list)
    plannedStart: str | None = None
    plannedEnd: str | None = None
    risk: Literal["Low", "Medium", "High"] | None = "Low"
    template: str | None = None
    rollback: str | None = ""


class MaintenanceBody(BaseModel):
    customer: str = ""
    devices: list[str] | None = None
    name: str
    start: str
    minutes: int
    collectData: bool = True
    changeId: int | None = None


class DeviceBody(BaseModel):
    customer: str = ""
    site: str
    name: str
    role: str
    platform: str = ""
    model: str = ""
    status: Literal["Active", "Planned"] = "Active"
    ip: str = ""
    serial: str = ""
    monitoring: bool = True


@router.post("/problems/{eventid}/acknowledge")
def acknowledge(eventid: str, request: Request, body: AckBody = Body(default_factory=AckBody)) -> dict:
    return act(request, actions.acknowledge, eventid, body.note)


@router.post("/problems/{eventid}/investigations")
def start_investigation(eventid: str, request: Request) -> dict:
    return act(request, investigate.start, eventid)


@router.post("/notes")
def note(body: NoteBody, request: Request) -> dict:
    return act(request, actions.post_note, body.itemType, body.itemId, body.text)


@router.post("/customers/{code}/resync")
def resync(code: str, request: Request) -> dict:
    return act(request, actions.resync, code)


@router.post("/customers/{code}/config-check")
def config_check(code: str, request: Request) -> dict:
    return act(request, actions.config_check, code)


@router.post("/customers/{code}/playbooks")
def playbook(code: str, body: PlaybookBody, request: Request) -> dict:
    return act(request, actions.run_playbook, code, str(body.templateId), body.devices, body.itemType,
               str(body.itemId), body.reason, body.variables)


@router.post("/customers/{code}/tickets")
def ticket(code: str, body: TicketBody, request: Request) -> dict:
    return act(request, actions.create_ticket, code, body.kind, body.title, body.description, body.priority,
               body.devices, planned_start=body.plannedStart, planned_end=body.plannedEnd, risk=body.risk or "Low",
               template=body.template or None, rollback=body.rollback or "")


@router.post("/customers/{code}/maintenance")
def maintenance(code: str, body: MaintenanceBody, request: Request) -> dict:
    return act(request, actions.create_maintenance, code, body.devices, body.name, body.start, body.minutes,
               body.collectData, body.changeId)


@router.post("/customers/{code}/devices")
def add_device(code: str, body: DeviceBody, request: Request) -> dict:
    return act(request, actions.add_device, code, body.site, body.name, body.role, body.platform, body.model,
               body.status, body.ip, body.serial, body.monitoring)


def not_found(request: Request) -> JSONResponse:
    return JSONResponse({"detail": f"No such address: {request.url.path}"}, status_code=404)
