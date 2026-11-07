"""Monthly customer report: number formats, the number check, drafts, approval and the printed page (kit 1.6.0)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
os.environ.setdefault("GLUE_MAPPINGS", str(HERE.parent / "mappings.yaml"))
os.environ.setdefault("CONSOLE_DATA_DIR", tempfile.mkdtemp(prefix="console-test-"))
os.environ.setdefault("CONSOLE_SESSION_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("MSP_DOMAIN", "msp.test.internal")

from msp_glue.common import Tenant  # noqa: E402
from msp_glue.console import actions, auth, data, reports, store  # noqa: E402
from msp_glue.settings import settings  # noqa: E402

ACME = Tenant(id="t1", name="PT ACME Retail Indonesia", code="acme",
              custom_fields={"msp_code": "acme", "msp_report_language": "id"})
GLOBEX = Tenant(id="t2", name="Globex Pte Ltd", code="globex", custom_fields={"msp_code": "globex",
                                                                               "msp_report_language": "en"})


def facts(language: str = "id") -> dict:
    return {
        "customer": {"code": "acme", "name": ACME.name, "tier": "gold", "regulated": False},
        "period": {"month": "2026-10", "label": reports.month_label("2026-10", language),
                   "start": "2026-10-01T00:00:00+07:00", "end": "2026-11-01T00:00:00+07:00"},
        "language": language,
        "availability": {"target_percent": 99.9, "achieved_percent": 99.874, "previous_month_percent": 99.95,
                         "downtime_minutes": 56.2, "met": False},
        "tickets": {"opened": 12, "solved": 11, "service_level_breaches": 1, "taken_late": 0, "resolved_late": 1,
                    "mean_hours_to_resolve": 3.4},
        "configuration": {"devices_checked": 9, "compliant_percent": 89, "backups_fresh_percent": 100,
                          "as_of": "2026-11-01T02:00"},
        "devices": {"active": 11},
        "problems": {"count": 3, "by_severity": {"High": 2, "Average": 1}, "total_minutes": 75,
                     "longest": [{"device": "acme-jkt-rtr01", "problem": "Unavailable by ICMP ping", "severity": "High",
                                  "started": "2026-10-14T09:12", "minutes": 48, "resolved": True}]},
        "paths": [{"collector": "acme-jkt-01", "target": "Microsoft 365", "segment": "application", "problems": 2,
                   "web_ms": 412.5, "worst_hour_web_ms": 1890.0}],
        "capacity": [{"device": "acme-jkt-rtr01", "interface": "Gi0/0/0", "circuit": "IOH-123", "capacity_mbps": 100.0,
                      "busy_hour_mbps": 76.5, "utilisation_percent": 76.5, "days_to_warning": 9.0, "status": "at_risk"}],
    }


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "console_data_dir", tmp_path)
    monkeypatch.setattr(store, "_ready", False)
    monkeypatch.setattr(data, "customers", lambda: [ACME, GLOBEX])
    monkeypatch.setattr(data, "now", lambda: datetime(2026, 11, 2, 9, 30, tzinfo=data.tz()))
    yield tmp_path
    store._ready = False


def engineer() -> auth.User:
    return auth.User(sub="s1", email="eko@ioh.example", name="Eko Test", roles=[auth.ENGINEER])


# ---------------------------------------------------------------------------- formats
def test_numbers_are_written_the_customers_way():
    assert reports.fmt(99.874, "id", 3) == "99,874" and reports.fmt(99.874, "en", 3) == "99.874"
    assert reports.fmt(1234.5, "id", 1) == "1.234,5" and reports.fmt(1234.5, "en", 1) == "1,234.5"
    assert reports.fmt(99.5, "id") == "99,5" and reports.fmt(100, "en") == "100" and reports.fmt(None, "id") == "-"
    assert reports.month_label("2026-10", "id") == "Oktober 2026" and reports.previous_month(
        datetime(2026, 1, 3, tzinfo=data.tz())) == "2025-12"


def test_numbers_in_the_text_must_come_from_the_facts():
    good = {"summary": "Ketersediaan 99,874% dari target 99,9%; gangguan 56,2 menit, 12 tiket dibuka pada Oktober 2026.",
            "availability": "", "service": "Rata-rata 3,4 jam.", "paths": "", "capacity": "Jalur Gi0/0/0 pada 76,5%.",
            "recommendations": ["Tinjau kapasitas dalam 9 hari."]}
    assert reports.check_numbers(good, facts()) == []
    bad = {**good, "service": "Rata-rata 4,1 jam dan 1.234 pengguna.", "recommendations": ["Naikkan ke 200 Mbps."]}
    flagged = reports.check_numbers(bad, facts())
    assert {f["number"] for f in flagged} == {"4,1", "1.234", "200"}
    assert {f["section"] for f in flagged} == {"service", "recommendations"}
    assert reports.check_numbers({"summary": "Availability was 99.874% (target 99.9%)."}, facts("en")) == []


@pytest.mark.parametrize("language", ["id", "en"])
def test_fixed_sentences_pass_their_own_check(language):
    text = reports.fixed_text(facts(language))
    assert reports.check_numbers(text, facts(language)) == []
    assert text["summary"] and text["availability"] and text["service"] and text["capacity"]
    if language == "id":
        assert "99,874%" in text["availability"] and "di bawah target" in text["availability"]
        assert any("Gi0/0/0" in r for r in text["recommendations"])
    else:
        assert "99.874%" in text["availability"] and "below the agreed target" in text["availability"]


# -------------------------------------------------------------------------- the flow
def test_draft_edit_approve_reopen(db, monkeypatch):
    monkeypatch.setattr(reports, "facts", lambda code, month: facts())
    monkeypatch.setattr(reports.investigate, "endpoint", lambda: ("http://model.test/v1", "qwen3:8b", ""))
    monkeypatch.setattr(reports, "ask_model", lambda f: {"summary": "Bulan ini 99,874% dan 7 kali gangguan.",
                                                         "availability": "", "service": "", "paths": "",
                                                         "capacity": "", "recommendations": []})
    report = reports.generate("acme", "2026-10")
    assert report["status"] == "draft" and report["writer"] == "qwen3:8b"
    assert report["flags"] == [{"section": "summary", "number": "7"}]       # 7 problems is not in the data
    user = engineer()
    result = reports.save_text(user, "acme", "2026-10", {"summary": "Bulan ini 99,874% dan 3 kali gangguan.",
                                                         "recommendations": ["Tinjau Gi0/0/0."]})
    assert "Text saved." in result["message"] and reports.get("acme", "2026-10")["flags"] == []
    reports.approve(user, "acme", "2026-10")
    approved = reports.get("acme", "2026-10")
    assert approved["status"] == "approved" and approved["approved_by"] == "Eko Test"
    with pytest.raises(actions.ActionError, match="Only a draft"):
        reports.save_text(user, "acme", "2026-10", {"summary": "x"})
    with pytest.raises(actions.ActionError, match="Reopen it"):
        reports.start(user, "acme", "2026-10")
    reports.reopen(user, "acme", "2026-10")
    assert reports.get("acme", "2026-10")["status"] == "draft"
    trail = [e["action"] for e in store.entries(customer="acme")]
    assert trail[:3] == ["report reopen", "report approve", "report edit"]


def test_a_model_that_fails_leaves_fixed_sentences(db, monkeypatch):
    monkeypatch.setattr(reports, "facts", lambda code, month: facts("en"))
    monkeypatch.setattr(reports.investigate, "endpoint", lambda: ("http://model.test/v1", "qwen3:8b", ""))

    def broken(_facts):
        raise RuntimeError("HTTP 503: busy")
    monkeypatch.setattr(reports, "ask_model", broken)
    report = reports.generate("acme", "2026-10")
    assert report["status"] == "draft" and report["writer"] == "fixed sentences"
    assert "HTTP 503" in report["error"] and "below the agreed target" in report["narrative"]["availability"]


def test_who_may_draft_and_which_months(db):
    with pytest.raises(actions.Forbidden):
        reports.start(auth.User(sub="x", email="a@b.c", name="A", roles=[auth.AUTOMATION]), "acme", "2026-10")
    with pytest.raises(actions.ActionError, match="has ended"):
        reports.start(engineer(), "acme", "2026-11")
    with pytest.raises(actions.ActionError, match="look like"):
        reports.start(engineer(), "acme", "2026-13")


def test_last_month_is_drafted_on_schedule(db, monkeypatch):
    seen = []
    monkeypatch.setattr(reports, "generate", lambda code, month, user=None, entry=None: seen.append((code, month)))
    assert reports.generate_due(datetime(2026, 11, 1, 1, 0, tzinfo=data.tz())) == []     # before 02:00
    assert reports.generate_due(datetime(2026, 11, 1, 2, 5, tzinfo=data.tz())) == ["acme", "globex"]
    assert seen == [("acme", "2026-10"), ("globex", "2026-10")]


def test_the_printed_page(db, monkeypatch):
    monkeypatch.setattr(reports, "facts", lambda code, month: facts())
    monkeypatch.setattr(reports.investigate, "endpoint", lambda: ("", "", ""))
    monkeypatch.setattr(data, "customer", lambda code: ACME if code == "acme" else None)
    reports.generate("acme", "2026-10")
    page = reports.render("acme", "2026-10")
    assert "Laporan Layanan Bulanan" in page and "Oktober 2026" in page and "DRAF: belum disetujui" in page
    assert "99,874%" in page and "Microsoft 365" in page and "IOH-123" in page and "acme-jkt-rtr01" in page
    assert "model bahasa" not in page                          # fixed sentences: no model was used
    reports.approve(engineer(), "acme", "2026-10")
    page = reports.render("acme", "2026-10")
    assert "DRAF" not in page and "Diperiksa dan disetujui oleh Eko Test pada 02-11-2026" in page
    assert reports.render("acme", "2026-09") is None


def test_list_of_the_month(db, monkeypatch):
    monkeypatch.setattr(reports, "facts", lambda code, month: facts())
    monkeypatch.setattr(reports.investigate, "endpoint", lambda: ("", "", ""))
    reports.generate("acme", "2026-10")
    page = reports.summary(None)
    assert page["month"] == "2026-10" and page["writer"] == {"model": None}
    rows = {r["code"]: r for r in page["rows"]}
    assert rows["acme"]["status"] == "draft" and rows["globex"]["status"] == "none"
    assert rows["globex"]["language"] == "en"
    assert page["monthsAvailable"][0]["month"] == "2026-10" and len(page["monthsAvailable"]) == 12


# ------------------------------------------------------------------------- path verdicts
def target(name, segment, state):
    return {"name": name, "segment": segment, "state": state}


def test_the_verdict_names_the_side_of_the_path():
    from msp_glue.console import observe
    edge, cf, google, m365 = ("IOH edge", "ioh"), ("Cloudflare", "internet"), ("Google", "internet"), ("Microsoft 365", "application")

    def side(*rows, online=True):
        return observe.verdict([target(n, s, st) for (n, s), st in rows], online)

    assert side((edge, "bad"), (cf, "bad"), (m365, "bad"))["side"] == "ioh"
    assert side((edge, "ok"), (cf, "warn"), (google, "bad"), (m365, "bad"))["side"] == "internet"
    # one public address that stops answering ping says nothing about the path
    assert side((edge, "ok"), (cf, "bad"), (google, "ok"), (m365, "ok"))["side"] == "none"
    app = side((edge, "ok"), (cf, "ok"), (m365, "warn"))
    assert app["side"] == "application" and "Microsoft 365" in app["text"]
    no_edge = side((cf, "bad"), (google, "bad"))
    assert no_edge["side"] == "internet" and "Add IOH's edge router" in no_edge["text"]
    assert side((edge, "ok"), online=False)["side"] == "collector"
    assert side((edge, "unknown"), (cf, "unknown"))["tone"] == "neutral"
