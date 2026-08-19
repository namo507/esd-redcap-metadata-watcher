"""Layer 0: calendar integration and the staleness policy.

Availability is the one input the engine cannot compute for itself, so it needs
an explicit policy for "how old is too old" and an explicit answer for "what do
we do when the sync fails". The rule that matters most: staleness is a *status*,
never a score penalty. Penalising a stale-but-ideal candidate makes them lose
for a reason that has nothing to do with fit, and it quietly corrupts the metric
that every other diagnostic is built on.

Freshness classes by how soon the visit is:

    horizon <= 72h    hard 15 min    soft 60 min
    3 - 14 days       hard 4 h       soft 24 h
    > 14 days         hard 24 h      soft 72 h

    age <= hard   -> fresh      normal scoring, auto-commit allowed
    hard < age <= soft -> stale -> score normally, mark the assignment
                                   provisional, block family notification,
                                   require a human confirmation
    age > soft / sync failed -> expired -> Layer 1 failure

Plus a write-time recheck: whatever the cache says, re-query the single winning
(coordinator, visit) pair immediately before commit. One API call, and it closes
the fifteen-minute race that produces the most embarrassing failure mode there
is, double-booking someone who accepted a meeting since the last sync.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import EngineConfig
from .models import BusyBlock, CalendarSnapshot, WorkingHours

FRESH = "fresh"
STALE = "stale"
EXPIRED = "expired"
SYNC_FAILED = "sync_failed"


# ---------------------------------------------------------------------------
# Staleness policy
# ---------------------------------------------------------------------------


def staleness_thresholds(horizon_hours: float, cfg: EngineConfig) -> Tuple[int, int]:
    """(hard, soft) seconds for a visit that is ``horizon_hours`` away."""
    if horizon_hours <= 72:
        return cfg.stale_hard_72h, cfg.stale_soft_72h
    if horizon_hours <= 24 * 14:
        return cfg.stale_hard_14d, cfg.stale_soft_14d
    return cfg.stale_hard_far, cfg.stale_soft_far


def classify_freshness(
    snapshot: Optional[CalendarSnapshot],
    now: datetime,
    horizon_hours: float,
    cfg: EngineConfig,
) -> Tuple[str, Optional[float]]:
    """Return (class, age_seconds). Missing snapshot counts as expired."""
    if snapshot is None:
        return EXPIRED, None
    if not snapshot.sync_ok:
        return SYNC_FAILED, snapshot.age_seconds(now)
    age = snapshot.age_seconds(now)
    hard, soft = staleness_thresholds(horizon_hours, cfg)
    if age <= hard:
        return FRESH, age
    if age <= soft:
        return STALE, age
    return EXPIRED, age


def systemic_failure(
    snapshots: Dict[str, Optional[CalendarSnapshot]],
    now: datetime,
    horizon_hours: float,
    cfg: EngineConfig,
) -> Optional[str]:
    """Circuit breaker.

    If more than ``unsyncable_halt_fraction`` of the team cannot be verified, the
    problem is the integration, not the roster. Halt the whole auto-assignment
    run and alert rather than quietly scheduling against a fifth of the team.
    """
    if not snapshots:
        return "no_calendar_data"
    bad = 0
    for snap in snapshots.values():
        cls, _ = classify_freshness(snap, now, horizon_hours, cfg)
        if cls in (EXPIRED, SYNC_FAILED):
            bad += 1
    fraction = bad / len(snapshots)
    if fraction > cfg.unsyncable_halt_fraction:
        return f"calendar_sync_degraded:{bad}/{len(snapshots)}"
    return None


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class CalendarProvider:
    """Interface. ``fetch`` returns one snapshot per coordinator."""

    name = "abstract"

    def fetch(
        self, coordinator_ids: Sequence[str], start: datetime, end: datetime
    ) -> Dict[str, CalendarSnapshot]:
        raise NotImplementedError


@dataclass
class MockProvider(CalendarProvider):
    """Deterministic provider for the pilot, tests and the demo dataset."""

    blocks: Dict[str, List[BusyBlock]]
    name: str = "mock"
    fail_for: Tuple[str, ...] = ()
    clock: Callable[[], datetime] = datetime.now

    def fetch(self, coordinator_ids, start, end):
        now = self.clock()
        out: Dict[str, CalendarSnapshot] = {}
        for cid in coordinator_ids:
            if cid in self.fail_for:
                out[cid] = CalendarSnapshot(
                    coordinator_id=cid,
                    provider=self.name,
                    fetched_at=now,
                    blocks=[],
                    sync_ok=False,
                    error_code="mock_forced_failure",
                )
                continue
            windowed = [
                b for b in self.blocks.get(cid, []) if b.overlaps(start, end)
            ]
            out[cid] = CalendarSnapshot(
                coordinator_id=cid, provider=self.name, fetched_at=now, blocks=windowed
            )
        return out


def _post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int = 20):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# availabilityView digit -> status, per the Microsoft Graph scheduleInformation
# reference. Note the two traps:
#   * 2 is BUSY and 0 is FREE. A widely-circulated Copilot answer states the
#     reverse ("busy is zero, available is three"). Building on that inverts the
#     whole scheduler: it books visits into exactly the slots people are busy,
#     and the output still looks like a plausible schedule.
#   * workingElsewhere is folded into 0 for backward compatibility, so it is
#     NOT distinguishable from free in the view string. Where that distinction
#     matters, read scheduleItems[].status instead.
_AVAILABILITY_VIEW = {
    "0": "free",
    "1": "tentative",
    "2": "busy",
    "3": "oof",
    "4": "workingElsewhere",  # legacy; current tenants emit 0
}

# Fields getSchedule may return that this tool must never see, store or render.
# Whether they arrive at all is decided by the Exchange calendar sharing level,
# not by our OAuth scope: at the tenant default of AvailabilityOnly the server
# omits them, and at LimitedDetails it sends them. We drop them either way, so
# that a sharing change made by one person cannot quietly widen what the lab's
# scheduling tool holds. See ESD-Graph-Privacy-RESEARCH-REPORT.md.
FORBIDDEN_EVENT_FIELDS = ("subject", "location", "isPrivate")

# The only scope this tool is permitted to run under. Delegated, read-only, and
# evaluated by Exchange against what the signed-in person can already see.
ALLOWED_SCOPES = frozenset({"Calendars.Read.Shared"})


class ScopeViolation(RuntimeError):
    """Raised at startup when a token grants more than free/busy reading."""


def _decode_jwt_claims(token: str) -> dict:
    """Read a JWT payload without verifying it.

    Verification is the token issuer's job and needs its signing keys. All we
    need here is to see what the token *claims* to grant, so we can refuse to
    run on an over-broad one. A forged token would fail at Graph anyway.
    """
    import base64

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, UnicodeDecodeError) as exc:
        raise ScopeViolation(f"could not read token claims: {exc}") from exc


def assert_least_privilege(token: str, allowed: Optional[frozenset] = None) -> dict:
    """Fail closed unless the token is delegated and free/busy only.

    Two checks, and the second is the one that matters most:

      * ``scp`` (delegated scopes) must be a subset of ALLOWED_SCOPES.
      * ``roles`` must be absent. A ``roles`` claim means application
        permissions are live, and app-only calendar access cannot be reduced
        below Calendars.Read, which reads subject and body. There is no
        Application Calendars.ReadBasic role.

    Returns the decoded claims so the caller can log the audit trail.
    """
    allowed = allowed or ALLOWED_SCOPES
    claims = _decode_jwt_claims(token)

    roles = claims.get("roles") or []
    if roles:
        raise ScopeViolation(
            "token carries application permissions "
            f"({', '.join(sorted(roles))}). App-only calendar access cannot be "
            "restricted to free/busy; refusing to run. Use delegated auth."
        )

    granted = set((claims.get("scp") or "").split())
    if not granted:
        raise ScopeViolation("token carries no delegated scopes (no 'scp' claim)")
    excess = granted - allowed
    if excess:
        raise ScopeViolation(
            f"token grants more than free/busy reading: {', '.join(sorted(excess))}. "
            f"Permitted: {', '.join(sorted(allowed))}."
        )
    return claims


def strip_event_details(entry: dict) -> dict:
    """Remove subject, location and isPrivate before anything downstream sees them."""
    return {k: v for k, v in entry.items() if k not in FORBIDDEN_EVENT_FIELDS}


@dataclass
class GraphProvider(CalendarProvider):
    """Microsoft Graph ``getSchedule``, free/busy only.

    Auth is **delegated**: the app acts as the signed-in person and therefore
    sees exactly what they already see in Outlook. Under the tenant default
    sharing level (AvailabilityOnly) Exchange does not put event subjects in the
    response at all, so titles are absent from the payload rather than merely
    ignored by this code.

    Application-only auth is deliberately unsupported. Microsoft publishes no
    calendar app role below ``Calendars.Read``, which reads subject and body, so
    app-only cannot satisfy the lab's constraint. ``enforce_scope`` refuses such
    a token at the first call.

    Pass a ``token_provider`` callable that handles refresh; nothing is cached here.
    """

    token_provider: Callable[[], str]
    mailbox_of: Dict[str, str]  # coordinator_id -> UPN / mailbox address
    organizer: str = "me"
    availability_view_interval: int = 15
    name: str = "msgraph"
    endpoint: str = "https://graph.microsoft.com/v1.0"
    enforce_scope: bool = True
    on_scope_checked: Optional[Callable[[dict], None]] = None

    def fetch(self, coordinator_ids, start, end):
        now = datetime.now(timezone.utc)
        token = self.token_provider()
        if self.enforce_scope:
            # Fail closed, and fail loudly. A scope problem is a privacy
            # problem, not a degraded-service problem, so it must not be
            # swallowed into a sync_ok=False snapshot.
            claims = assert_least_privilege(token)
            if self.on_scope_checked:
                self.on_scope_checked(claims)
        url = f"{self.endpoint}/users/{self.organizer}/calendar/getSchedule"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        }
        schedules = [self.mailbox_of[c] for c in coordinator_ids if c in self.mailbox_of]
        payload = {
            "schedules": schedules,
            "startTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "endTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "availabilityViewInterval": self.availability_view_interval,
        }
        try:
            data = _post_json(url, payload, headers)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            return {
                cid: CalendarSnapshot(
                    coordinator_id=cid,
                    provider=self.name,
                    fetched_at=now,
                    sync_ok=False,
                    error_code=type(exc).__name__,
                )
                for cid in coordinator_ids
            }

        by_mailbox = {item.get("scheduleId"): item for item in data.get("value", [])}
        out: Dict[str, CalendarSnapshot] = {}
        for cid in coordinator_ids:
            mailbox = self.mailbox_of.get(cid)
            item = by_mailbox.get(mailbox)
            if item is None:
                out[cid] = CalendarSnapshot(
                    coordinator_id=cid,
                    provider=self.name,
                    fetched_at=now,
                    sync_ok=False,
                    error_code="mailbox_not_returned",
                )
                continue
            blocks = []
            for raw_entry in item.get("scheduleItems", []):
                entry = strip_event_details(raw_entry)
                blocks.append(
                    BusyBlock(
                        start=_parse_graph_dt(entry["start"]),
                        end=_parse_graph_dt(entry["end"]),
                        status=entry.get("status", "busy"),
                    )
                )
            out[cid] = CalendarSnapshot(
                coordinator_id=cid,
                provider=self.name,
                fetched_at=now,
                blocks=blocks,
                # Free time only counts inside the declared working envelope.
                # getSchedule returns this in the same call, at no extra cost.
                working_hours=WorkingHours.from_graph(item.get("workingHours")),
            )
        return out


def _parse_graph_dt(node: dict) -> datetime:
    raw = node["dateTime"].split(".")[0]
    return datetime.fromisoformat(raw)


@dataclass
class GoogleProvider(CalendarProvider):
    """Google Calendar ``freeBusy.query``.

    freeBusy returns busy intervals only, with no status detail, so every block
    is treated as a hard ``busy``. That is the safe reading, and it is a reason
    to prefer Graph where the lab has a choice.
    """

    token_provider: Callable[[], str]
    calendar_of: Dict[str, str]  # coordinator_id -> calendarId (usually the email)
    name: str = "google"
    endpoint: str = "https://www.googleapis.com/calendar/v3/freeBusy"

    def fetch(self, coordinator_ids, start, end):
        now = datetime.now(timezone.utc)
        headers = {
            "Authorization": f"Bearer {self.token_provider()}",
            "Content-Type": "application/json",
        }
        payload = {
            "timeMin": start.isoformat() + ("Z" if start.tzinfo is None else ""),
            "timeMax": end.isoformat() + ("Z" if end.tzinfo is None else ""),
            "items": [
                {"id": self.calendar_of[c]} for c in coordinator_ids if c in self.calendar_of
            ],
        }
        try:
            data = _post_json(self.endpoint, payload, headers)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            return {
                cid: CalendarSnapshot(
                    coordinator_id=cid,
                    provider=self.name,
                    fetched_at=now,
                    sync_ok=False,
                    error_code=type(exc).__name__,
                )
                for cid in coordinator_ids
            }

        calendars = data.get("calendars", {})
        out: Dict[str, CalendarSnapshot] = {}
        for cid in coordinator_ids:
            cal_id = self.calendar_of.get(cid)
            node = calendars.get(cal_id)
            if node is None or node.get("errors"):
                out[cid] = CalendarSnapshot(
                    coordinator_id=cid,
                    provider=self.name,
                    fetched_at=now,
                    sync_ok=False,
                    error_code="freebusy_error",
                )
                continue
            blocks = [
                BusyBlock(
                    start=datetime.fromisoformat(b["start"].replace("Z", "")),
                    end=datetime.fromisoformat(b["end"].replace("Z", "")),
                    status="busy",
                )
                for b in node.get("busy", [])
            ]
            out[cid] = CalendarSnapshot(
                coordinator_id=cid, provider=self.name, fetched_at=now, blocks=blocks
            )
        return out


def provider_from_env(mock_blocks: Optional[Dict[str, List[BusyBlock]]] = None):
    """Pick a provider from environment configuration.

    ESD_CAL_PROVIDER = msgraph | google | mock (default mock)
    ESD_CAL_TOKEN    = bearer token for the chosen provider
    ESD_CAL_MAP      = JSON path mapping coordinator_id -> mailbox / calendarId
    """
    kind = os.environ.get("ESD_CAL_PROVIDER", "mock").lower()
    if kind == "mock":
        return MockProvider(blocks=mock_blocks or {})

    token = os.environ.get("ESD_CAL_TOKEN")
    map_path = os.environ.get("ESD_CAL_MAP")
    if not token or not map_path or not os.path.exists(map_path):
        raise RuntimeError(
            "ESD_CAL_TOKEN and ESD_CAL_MAP must be set for a live calendar provider"
        )
    with open(map_path, "r", encoding="utf-8") as fh:
        mapping = json.load(fh)

    if kind == "msgraph":
        return GraphProvider(token_provider=lambda: token, mailbox_of=mapping)
    if kind == "google":
        return GoogleProvider(token_provider=lambda: token, calendar_of=mapping)
    raise ValueError(f"unknown ESD_CAL_PROVIDER {kind!r}")


# ---------------------------------------------------------------------------
# Write-time recheck
# ---------------------------------------------------------------------------


def write_time_recheck(
    provider: CalendarProvider,
    coordinator_id: str,
    slot_start: datetime,
    slot_end: datetime,
) -> Tuple[bool, str]:
    """Re-query one coordinator immediately before commit.

    Returns (ok_to_commit, detail). A failure here is not a scoring problem, it
    is a race: re-rank and try the next candidate.
    """
    snaps = provider.fetch([coordinator_id], slot_start, slot_end)
    snap = snaps.get(coordinator_id)
    if snap is None or not snap.sync_ok:
        return False, "recheck_sync_failed"
    for block in snap.hard_blocks():
        if block.overlaps(slot_start, slot_end):
            return False, f"write_time_conflict:{block.status}"
    return True, "ok"
