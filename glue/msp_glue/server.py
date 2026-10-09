"""Webhook receiver and scheduler.

Endpoints
  GET  /healthz          liveness check
  POST /hooks/nautobot   tenant and device changes (signed with NAUTOBOT_WEBHOOK_SECRET)
  POST /hooks/glpi       change updates (signed with GLPI_WEBHOOK_SECRET)
  POST /run/{job}        manual trigger: reconcile, compliance (needs the Nautobot token as bearer)

All work runs on one background worker thread, one job at a time, so two webhooks can
never update the same object at the same moment.
"""

from __future__ import annotations

import hashlib
import hmac
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
app = FastAPI(title="msp-glue", version="1.0")
_jobs: "queue.Queue[tuple[str, callable, tuple]]" = queue.Queue()
_state = {"last_reconcile": None, "last_error": None}


def _worker() -> None:
    while True:
        name, func, args = _jobs.get()
        started = time.time()
        try:
            with exclusive():
                result = func(*args)
            log.info("Job %s done in %.1fs: %s", name, time.time() - started, result)
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = f"{name}: {exc}"
            log.exception("Job %s failed", name)
        finally:
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


def enqueue(name: str, func, *args) -> None:
    _jobs.put((name, func, args))


def reconcile() -> str:
    for tenant in tenants.managed_tenants():
        tenants.ensure_tenant(tenant)
    report = devices.sync_all()
    _state["last_reconcile"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return report.summary()


@app.on_event("startup")
def _start() -> None:
    threading.Thread(target=_worker, daemon=True, name="worker").start()
    if settings.reconcile_interval > 0:
        threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "queue": _jobs.qsize(), **_state}


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
@app.post("/run/{job}")
def run_job(job: str, request: Request) -> dict:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, settings.nautobot_token):
        raise HTTPException(status_code=401, detail="unauthorised")
    jobs = {"reconcile": reconcile, "compliance": compliance.run}
    if job not in jobs:
        raise HTTPException(status_code=404, detail=f"unknown job, use one of {sorted(jobs)}")
    enqueue(job, jobs[job])
    return {"queued": job}
