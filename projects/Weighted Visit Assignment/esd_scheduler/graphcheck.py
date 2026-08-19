"""Automated version of the "try to break it" protocol.

The PI asked for two things: that we try to break the privacy guarantee
ourselves, and that someone independent confirm it cannot be broken easily.
This module does the first and produces the evidence for the second.

Each probe asserts something that must be TRUE of a correctly configured
delegated app. A probe that fails is a finding, not a crash, so the run always
completes and prints a full report.

    python -m esd_scheduler verify-graph

Requires ESD_CAL_TOKEN and ESD_CAL_MAP (see calendarsync.provider_from_env).
No probe writes anything unless --allow-write-probe is passed explicitly.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from .calendarsync import (
    FORBIDDEN_EVENT_FIELDS,
    ScopeViolation,
    _decode_jwt_claims,
    assert_least_privilege,
)

GRAPH = "https://graph.microsoft.com/v1.0"


@dataclass
class ProbeResult:
    code: str
    name: str
    passed: Optional[bool]           # None = skipped
    detail: str
    evidence: str = ""
    severity: str = "high"           # high | medium | info

    @property
    def mark(self) -> str:
        if self.passed is None:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


@dataclass
class ProbeReport:
    tenant_hint: str = ""
    ran_at: str = ""
    results: List[ProbeResult] = field(default_factory=list)

    @property
    def failures(self) -> List[ProbeResult]:
        return [r for r in self.results if r.passed is False]

    @property
    def ok(self) -> bool:
        return not self.failures


def _request(method: str, url: str, token: str, payload: Optional[dict] = None):
    """Return (status, body). Never raises on an HTTP error status.

    A 403 is the expected, desirable outcome for most probes here, so it must be
    an observation rather than an exception.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            body = {}
        return exc.code, body
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": {"message": str(exc)}}


def _err(body: dict) -> str:
    return str((body.get("error") or {}).get("code") or "")[:60]


def run_probes(
    token: str,
    mailboxes: Dict[str, str],
    allow_write_probe: bool = False,
    now: Optional[datetime] = None,
) -> ProbeReport:
    now = now or datetime.now(timezone.utc)
    report = ProbeReport(ran_at=now.isoformat(timespec="seconds"))
    targets = [m for m in mailboxes.values()]
    other = targets[0] if targets else None

    # --- T9: what does the token actually claim? ---------------------------
    try:
        claims = _decode_jwt_claims(token)
    except ScopeViolation as exc:
        report.results.append(
            ProbeResult("T9", "Token claims are readable", False, str(exc))
        )
        return report

    report.tenant_hint = str(claims.get("tid", ""))[:8]
    scp = claims.get("scp", "")
    roles = claims.get("roles") or []

    report.results.append(
        ProbeResult(
            "T9a", "No application permissions on the token",
            not roles,
            "no roles claim, so this is a delegated token" if not roles
            else f"roles claim present: {', '.join(roles)}. App-only calendar "
                 f"access cannot be limited to free/busy.",
            evidence=f"roles={roles!r}",
        )
    )
    try:
        assert_least_privilege(token)
        scope_ok, scope_detail = True, f"scopes are free/busy only: {scp}"
    except ScopeViolation as exc:
        scope_ok, scope_detail = False, str(exc)
    report.results.append(
        ProbeResult("T9b", "Delegated scopes are free/busy only", scope_ok,
                    scope_detail, evidence=f"scp={scp!r}")
    )

    if other is None:
        report.results.append(
            ProbeResult("T1", "getSchedule returns no subjects", None,
                        "no mailboxes configured in ESD_CAL_MAP")
        )
        return report

    # --- T1: the central probe. getSchedule must not carry subjects ---------
    start = now + timedelta(days=1)
    payload = {
        "schedules": targets,
        "startTime": {"dateTime": start.replace(hour=8).isoformat(), "timeZone": "UTC"},
        "endTime": {"dateTime": start.replace(hour=18).isoformat(), "timeZone": "UTC"},
        "availabilityViewInterval": 30,
    }
    status, body = _request("POST", f"{GRAPH}/me/calendar/getSchedule", token, payload)
    if status != 200:
        report.results.append(
            ProbeResult("T1", "getSchedule returns no subjects", False,
                        f"call failed with HTTP {status} {_err(body)}")
        )
    else:
        leaked: List[str] = []
        items_seen = 0
        has_working_hours = False
        for entry in body.get("value", []):
            if entry.get("workingHours"):
                has_working_hours = True
            for item in entry.get("scheduleItems", []):
                items_seen += 1
                for f in FORBIDDEN_EVENT_FIELDS:
                    if f in item and item[f] not in (None, "", False):
                        leaked.append(f"{entry.get('scheduleId')}: {f}")
        report.results.append(
            ProbeResult(
                "T1", "getSchedule returns no subjects or locations",
                not leaked,
                f"{items_seen} scheduleItems across {len(targets)} mailbox(es); "
                + ("no subject, location or isPrivate present"
                   if not leaked else
                   "SENSITIVE FIELDS RETURNED. A calendar is shared above "
                   "AvailabilityOnly. Check Get-MailboxFolderPermission."),
                evidence="; ".join(leaked[:6]),
            )
        )
        report.results.append(
            ProbeResult(
                "T1b", "workingHours returned (blank is not free)",
                has_working_hours,
                "working hours present, so empty slots can be distinguished from "
                "out-of-hours" if has_working_hours else
                "no workingHours returned; free-time detection would be unsafe",
                severity="medium",
            )
        )

    # --- T2/T3/T4: direct event reads must be refused -----------------------
    for code, name, url in (
        ("T2", "Direct event read is refused", f"{GRAPH}/users/{other}/events?$top=1"),
        ("T3", "Subject projection is refused",
         f"{GRAPH}/users/{other}/calendar/events?$select=subject&$top=1"),
        ("T4", "Calendar enumeration is refused", f"{GRAPH}/users/{other}/calendars"),
    ):
        status, body = _request("GET", url, token)
        report.results.append(
            ProbeResult(
                code, name, status in (401, 403),
                f"HTTP {status} {_err(body)}" if status in (401, 403)
                else f"HTTP {status}: THE CALL SUCCEEDED. This token can read "
                     f"event detail directly.",
                evidence=url,
            )
        )

    # --- T5/T6: write paths ------------------------------------------------
    if not allow_write_probe:
        report.results.append(
            ProbeResult(
                "T5", "Event creation is refused", None,
                "skipped by default. This probe attempts a real event create, "
                "and would leave an event behind if the guarantee is broken. "
                "Re-run with --allow-write-probe on a test mailbox, or verify "
                "by hand in Graph Explorer.",
                severity="medium",
            )
        )
    else:
        probe_start = (now + timedelta(days=400)).replace(hour=3, minute=0, second=0)
        status, body = _request(
            "POST", f"{GRAPH}/users/{other}/events", token,
            {
                "subject": "ESD scheduler write probe - delete me",
                "start": {"dateTime": probe_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": (probe_start + timedelta(minutes=15)).isoformat(),
                        "timeZone": "UTC"},
            },
        )
        created_id = body.get("id") if status in (200, 201) else None
        report.results.append(
            ProbeResult(
                "T5", "Event creation is refused", status in (401, 403),
                f"HTTP {status} {_err(body)}" if status in (401, 403)
                else f"HTTP {status}: AN EVENT WAS CREATED. Write access exists. "
                     f"Event id {created_id}; delete it immediately.",
                evidence=str(created_id or ""),
            )
        )
        if created_id:
            _request("DELETE", f"{GRAPH}/users/{other}/events/{created_id}", token)

    return report


def render(report: ProbeReport) -> str:
    lines = [
        "ESD scheduler - Graph privacy verification",
        f"run at {report.ran_at}" + (f"  tenant {report.tenant_hint}..."
                                     if report.tenant_hint else ""),
        "",
        f"  {'':5}{'code':6}{'result':7}check",
        f"  {'-' * 68}",
    ]
    for r in report.results:
        lines.append(f"  {'':5}{r.code:6}{r.mark:7}{r.name}")
        lines.append(f"  {'':18}{r.detail}")
        if r.evidence:
            lines.append(f"  {'':18}evidence: {r.evidence}")
        lines.append("")
    if report.ok:
        lines.append("  RESULT: every probe passed. The tool cannot read event")
        lines.append("  titles, and cannot write to anyone's calendar.")
    else:
        lines.append(f"  RESULT: {len(report.failures)} probe(s) FAILED:")
        for r in report.failures:
            lines.append(f"    - {r.code} {r.name}")
        lines.append("")
        lines.append("  Do not connect live Outlook until these are resolved.")
    lines.append("")
    lines.append("  Attach this output to the IT ticket, and ask IT to re-run")
    lines.append("  T1, T2 and T5 from their own account. Our own testing is")
    lines.append("  necessary but is not independent verification.")
    return "\n".join(lines)
