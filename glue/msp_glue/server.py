"""Webhook receiver and scheduler.

Endpoints
  GET  /healthz              liveness check
  POST /hooks/nautobot       tenant and device changes (signed with NAUTOBOT_WEBHOOK_SECRET)
  POST /hooks/glpi           change updates (signed with GLPI_WEBHOOK_SECRET)
  POST /run/{job}            manual trigger: reconcile, compliance        } these three need the
  POST /run/{job}/{code}     tenant (provision or repair one customer),    } Nautobot token as bearer
                             devices (push one customer's devices)        } (the MSP Console sends it)
  GET  /jobs                 the last 50 jobs with their result

All work runs on one background worker thread, one job at a time, so two webhooks can
never update the same object at the same moment.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import itertools
import json
import logging
import queue
import threading
import time

from fastapi import FastAPI, HTTPException, Request

from . import changes, compliance, devices, tenants
from .common import DEVICE_FIELDS, TENANT_FIELDS
from .locking import exclusive
from .settings import settings

log = logging.getLogger("msp_glue.server")
app = FastAPI(title="msp-glue", version="1.1")
_jobs: "queue.Queue[tuple[dict, callable, tuple]]" = queue.Queue()
_state = {"last_reconcile": None, "last_error": None}
_history: "collections.deque[dict]" = collections.deque(maxlen=50)
_ids = itertools.count(1)
# Jobs whose work a full reconcile does again (job names start with these words).
SYNC_JOBS = ("reconcile", "tenant ", "device ", "devices ", "retire ")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _worker() -> None:
    while True:
        record, func, args = _jobs.get()
        started = time.time()
        record.update(status="running", started=_now())
        try:
            with exclusive():
                result = func(*args)
            record.update(status="done", result=str(result)[:500])
            if (_state["last_error"] or "").startswith(record["name"] + ":"):
                _state["last_error"] = None   # the same job went through this time
            log.info("Job %s done in %.1fs: %s", record["name"], time.time() - started, result)
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = f"{record['name']}: {exc}"
            record.update(status="failed", result=f"{type(exc).__name__}: {exc}"[:500])
            log.exception("Job %s failed", record["name"])
        finally:
            record["finished"] = _now()
            record["seconds"] = round(time.time() - started, 1)
            _jobs.task_done()


def _scheduler() -> None:
    # First run shortly after start-up, then every GLUE_RECONCILE_INTERVAL seconds.
    # Configuration drift tickets are refreshed every GLUE_COMPLIANCE_INTERVAL seconds.
    time.sleep(30)
    last_compliance = 0.0
    while True:
        enqueue("reconcile", reconcile)
        if time.time() - last_compliance >= settings.compliance_interval:
            enqueue("compliance", compliance.run)
            last_compliance = time.time()
        time.sleep(max(60, settings.reconcile_interval))


def enqueue(name: str, func, *args, requested_by: str = "") -> dict:
    record = {"id": next(_ids), "name": name, "status": "queued", "queued": _now(), "requested_by": requested_by}
    _history.appendleft(record)
    _jobs.put((record, func, args))
    return record


def reconcile() -> str:
    for tenant in tenants.managed_tenants():
        tenants.ensure_tenant(tenant)
    report = devices.sync_all()
    _state["last_reconcile"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # A full sync repairs what earlier sync jobs left undone, so their error is no longer open. Errors of
    # other jobs (compliance, GLPI changes) stay until their own job succeeds or fails again.
    if report.errors:
        # Not "reconcile: ...": the worker clears that form as soon as this job returns.
        _state["last_error"] = f"reconcile found {len(report.errors)} device error(s), first: {report.errors[0]}"[:500]
    elif (_state["last_error"] or "").startswith(SYNC_JOBS):
        _state["last_error"] = None
    return report.summary()


@app.on_event("startup")
def _start() -> None:
    threading.Thread(target=_worker, daemon=True, name="worker").start()
    if settings.reconcile_interval > 0:
        threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "queue": _jobs.qsize(), "version": app.version, **_state}


# ------------------------------------------------------------------ Nautobot
@app.post("/hooks/nautobot")
async def nautobot_hook(request: Request) -> dict:
    body = await request.body()
    if settings.nautobot_webhook_secret:
        expected = hmac.new(settings.nautobot_webhook_secret.encode(), body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hook-Signature", "")):
            raise HTTPException(status_code=401, detail="bad signature")
    event = json.loads(body)
    model, action, data = event.get("model"), event.get("event"), event.get("data") or {}
    log.info("Nautobot webhook: %s %s %s", model, action, data.get("name") or data.get("display") or data.get("id"))
    log.debug("Nautobot webhook differences: %s", json.dumps((event.get("snapshots") or {}).get("differences"))[:1500])
    if model == "tenant" and action in ("created", "updated"):
        if _only_glue_fields_changed(event):
            return {"queued": False, "reason": "write-back from msp-glue"}
        tenant = tenants.get_tenant(data.get("id", ""))
        if tenant:
            enqueue(f"tenant {tenant.code}", tenants.ensure_tenant, tenant)
    elif model == "device" and action in ("created", "updated"):
        if _only_glue_fields_changed(event):
            return {"queued": False, "reason": "write-back from msp-glue"}
        enqueue(f"device {data.get('name')}", devices.sync_device, data["id"])
    elif model == "device" and action == "deleted":
        enqueue(f"retire {data.get('name')}", devices.retire_device, data["id"])
    return {"queued": True}


GLUE_OWNED_FIELDS = {k for k, v in {**TENANT_FIELDS, **DEVICE_FIELDS}.items() if v.get("managed")}


def _only_glue_fields_changed(event: dict) -> bool:
    """Ignore update events caused by msp-glue writing its own custom fields back.

    Compares the before and after snapshots field by field. Related objects are compared by id
    only, because their nested content (for example the tenant's last_updated) can differ
    between snapshots without the device itself changing.
    """
    snapshots = event.get("snapshots") or {}
    pre, post = snapshots.get("prechange") or {}, snapshots.get("postchange") or {}
    if not pre or not post:
        return False
    changed = set()
    for key in set(pre) | set(post):
        before, after = pre.get(key), post.get(key)
        if key == "custom_fields":
            before, after = before or {}, after or {}
            cf_changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
            if cf_changed - GLUE_OWNED_FIELDS:
                return False
            continue
        if isinstance(before, dict) and isinstance(after, dict) and "id" in before and "id" in after:
            if before["id"] != after["id"]:
                changed.add(key)
        elif before != after:
            changed.add(key)
    return not (changed - {"last_updated", "secrets_group"})


# ---------------------------------------------------------------------- GLPI
@app.post("/hooks/glpi")
async def glpi_hook(request: Request) -> dict:
    body = await request.body()
    if settings.glpi_webhook_secret:
        timestamp = request.headers.get("X-GLPI-timestamp", "")
        expected = hmac.new(settings.glpi_webhook_secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-GLPI-signature", "")):
            raise HTTPException(status_code=401, detail="bad signature")
        if timestamp and abs(time.time() - int(timestamp)) > 600:
            raise HTTPException(status_code=401, detail="stale webhook")
    event = json.loads(body)
    item = event.get("item") or {}
    if item.get("id"):
        enqueue(f"glpi change {item['id']}", changes.handle_change, int(item["id"]))
    return {"queued": bool(item.get("id"))}


@app.get("/hooks/glpi")
def glpi_challenge(crc_token: str = "") -> str:
    """Answers GLPI's optional challenge-response check when the webhook is saved."""
    if not settings.glpi_webhook_secret:
        return ""
    return hmac.new(settings.glpi_webhook_secret.encode(), crc_token.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------- manual triggers
def _authorise(request: Request) -> str:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, settings.nautobot_token):
        raise HTTPException(status_code=401, detail="unauthorised")
    # Who asked, for the job list (the console passes the signed-in engineer's e-mail address).
    return request.headers.get("X-Requested-By", "")[:120]


@app.post("/run/{job}")
def run_job(job: str, request: Request) -> dict:
    who = _authorise(request)
    jobs = {"reconcile": reconcile, "compliance": compliance.run}
    if job not in jobs:
        raise HTTPException(status_code=404, detail=f"unknown job, use one of {sorted(jobs)}")
    record = enqueue(job, jobs[job], requested_by=who)
    return {"queued": job, "job": record["id"]}


@app.post("/run/{job}/{code}")
def run_customer_job(job: str, code: str, request: Request) -> dict:
    who = _authorise(request)
    if job not in ("tenant", "devices"):
        raise HTTPException(status_code=404, detail="unknown job, use tenant or devices")
    tenant = tenants.get_tenant(code)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"customer {code} not found")
    if job == "tenant":
        record = enqueue(f"tenant {tenant.code}", tenants.ensure_tenant, tenant, requested_by=who)
    else:
        record = enqueue(f"devices {tenant.code}", lambda t: devices.sync_tenant(t).summary(), tenant,
                         requested_by=who)
    return {"queued": record["name"], "job": record["id"]}


@app.get("/jobs")
def jobs(request: Request) -> dict:
    _authorise(request)
    return {"jobs": list(_history), "queue": _jobs.qsize(), **_state}
