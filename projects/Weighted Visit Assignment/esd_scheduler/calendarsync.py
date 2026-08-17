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
from .models import BusyBlock, CalendarSnapshot

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


_GRAPH_STATUS = {
    "0": "free",
    "1": "tentative",
    "2": "busy",
    "3": "oof",
    "4": "workingElsewhere",
}


@dataclass
class GraphProvider(CalendarProvider):
    """Microsoft Graph ``getSchedule``.

    Needs an app registration with Calendars.Read (application permission) and a
    bearer token supplied by ``token_provider``. Nothing here caches the token;
    pass a callable that handles refresh.
    """

    token_provider: Callable[[], str]
    mailbox_of: Dict[str, str]  # coordinator_id -> UPN / mailbox address
    organizer: str = "me"
    availability_view_interval: int = 15
    name: str = "msgraph"
    endpoint: str = "https://graph.microsoft.com/v1.0"

    def fetch(self, coordinator_ids, start, end):
        now = datetime.now(timezone.utc)
        url = f"{self.endpoint}/users/{self.organizer}/calendar/getSchedule"
        headers = {
            "Authorization": f"Bearer {self.token_provider()}",
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
            for entry in item.get("scheduleItems", []):
                status = entry.get("status", "busy")
                blocks.append(
                    BusyBlock(
                        start=_parse_graph_dt(entry["start"]),
                        end=_parse_graph_dt(entry["end"]),
                        status=status,
                    )
                )
            out[cid] = CalendarSnapshot(
                coordinator_id=cid, provider=self.name, fetched_at=now, blocks=blocks
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
