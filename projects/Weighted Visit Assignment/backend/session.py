"""In-memory lab session: the state the board reads and writes.

One process holds one lab. The engine is deterministic and the audit store is
append-only SQLite, so a restart rebuilds the same synthetic lab and keeps every
decision already recorded.

Nothing here invents scheduling logic. Every judgement comes from
``esd_scheduler``; this module only decides what the screen needs to see.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from esd_scheduler import __version__ as ENGINE_VERSION
from esd_scheduler.calendar_import import (
    TIER_MONTH_GRID,
    TIER_RULES,
    ColorMap,
    import_pdf,
    suggest_roster_matches,
)
from esd_scheduler.calendarsync import MockProvider
from esd_scheduler.config import EngineConfig, load_config
from esd_scheduler.demo import build_lab
from esd_scheduler.lab import build_live
from esd_scheduler.drift import weekly_drift
from esd_scheduler import eligibility
from esd_scheduler.calendar_roles import ROLE_LABEL, ROLE_MEANING, POLARITY
from esd_scheduler.constraints import (
    ROUTE_MANUAL,
    ROUTE_REMOTE,
    ReliabilityMatrix,
    check_candidate,
    resource_blockers,
    resource_checks,
    evidence_state,
    offer_window,
    route_visit,
    visit_duration_hours,
)
from esd_scheduler.availability import coverage_report, week_grid
from esd_scheduler.pairing import rank_pairs
from esd_scheduler.roster import Roster
from esd_scheduler.schedule import (
    STATUS_LABEL, VISIT_STATUSES, ProtocolSchedule, upcoming)
from esd_scheduler.scoring import ramped_capacity
from esd_scheduler.engine import (
    commit_assignment,
    fairness_violations,
    plan_week,
    score_visit,
)
from esd_scheduler.models import (
    BusyBlock, CalendarSnapshot, CompletedVisit, Family, Visit)
from esd_scheduler.store import OVERRIDE_REASON_CODES, AuditStore

UPLOAD_DIR = os.path.join("data", "uploads")
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

# The four criteria, named the way a coordinator would say them out loud.
# The engine's own names (continuity, burden relief, protocol continuity) are
# precise but they are analyst vocabulary, and a board that has to be explained
# before it can be used is a board nobody uses.
CRITERION_LABEL = {
    "phi": "Knows the family",
    "omega": "Family's choice",
    "psi": "Has room this week",
    "p": "Did the last visit",
}
CRITERION_HELP = {
    "phi": "Has visited this family before, and how recently.",
    "omega": "Whether the family asked for this person.",
    "psi": "How much of their week is still free, counting travel as work.",
    "p": "Ran this family's previous visit in the same study.",
}
FAIL_REASON_TEXT = {
    "family_exclusion": "Family exclusion on file",
    "onboarding_cap": "At the onboarding limit for this week",
    "no_working_hours_in_window": "No working hours inside the visit window",
    "no_open_slot": "No free block long enough, including travel",
    "calendar_clash": "Calendar is booked across the whole window",
    "calendar_unavailable:expired": "Calendar too stale to trust",
    "calendar_unavailable:sync_failed": "Calendar sync failed",
}
VETO_TEXT = {
    "over_capacity": "Already at their capacity for the week",
    "travel_share_cap": "Over their share of the driving, and this is a long trip",
}


def describe_fail(reason: Optional[str]) -> str:
    if not reason:
        return "Not eligible"
    if reason.startswith("missing_credential:"):
        missing = reason.split(":", 1)[1].replace(",", ", ")
        return f"Not certified for {missing}"
    return FAIL_REASON_TEXT.get(reason, reason.replace("_", " "))


def reason_string(cand, family, coordinator) -> str:
    """Why the board would send this person, in one sentence.

    Master §3: "Every ranked result must render its reason string" - matching
    the site's own promise that it "explains who it would send". A name and a
    bar is not an explanation.
    """
    bits: List[str] = []
    if cand.coordinator_id in family.preferred_coordinators:
        bits.append("the family asked for them")
    if cand.components.p >= 1.0:
        bits.append("did this family's last visit")
    elif cand.components.k_prior_visits > 0:
        n = cand.components.k_prior_visits
        bits.append(f"has seen this family {n} time{'s' if n > 1 else ''}")
    if coordinator is not None and coordinator.van_trained:
        bits.append("van-trained")
    util = cand.components.utilization
    if util <= 0.6:
        bits.append("room left this week")
    elif util >= 0.9:
        bits.append("close to full this week")
    if coordinator is not None and coordinator.out_of_hours_count == 0:
        bits.append("no recent out-of-hours load")
    if not bits:
        bits.append("no conflicts and capacity available")
    return "Recommended: " + "; ".join(bits) + "."


def confidence_words(stability: Optional[float]) -> str:
    """Never show a coordinator a probability. Show them a judgement."""
    if stability is None:
        return "Only option"
    if stability >= 0.85:
        return "Clear choice"
    if stability >= 0.60:
        return "Slight edge"
    return "Too close to call"


def _loads(raw):
    import json as _json

    if not raw:
        return []
    try:
        return _json.loads(raw)
    except (TypeError, ValueError):
        return []


DEMO = "demo"
LIVE = "live"


def alias_of(entry) -> Optional[str]:
    """The other name this person goes by, or None if they only have one.

    Somebody can be printed on the Outlook export under one name and written
    in the manual under another. Showing only one of them leaves a reader
    matching the two documents up by guesswork, and showing a different one
    in each part of the board is how one member of staff comes to look like
    two. So wherever the board names a person it names them the same way.
    """
    if entry is None:
        return None
    manual = getattr(entry, "manual_name", None)
    name = getattr(entry, "name", "") or ""
    if manual and manual.lower() not in name.lower():
        return manual
    return None


def board_mode() -> str:
    """Which lab the board builds: the synthetic one, or the real roster only.

    Demo stays the default so an existing install, a test and the published
    offline snapshot all behave exactly as before. Set ESD_MODE=live to start
    from the roster with no invented families, visits or busy time.
    """
    value = (os.environ.get("ESD_MODE") or DEMO).strip().lower()
    return LIVE if value == LIVE else DEMO


def week_epoch(now: datetime) -> datetime:
    """The Monday 09:00 this week's board is anchored to, never in the future.

    The synthetic lab is built against this so its visits land in the current
    week. Anchoring on Monday 09:00 is fine for all but nine hours of the week:
    between midnight and 09:00 on a Monday, that instant has not happened yet.

    A lab anchored ahead of the clock stamps its calendar evidence in the
    future, the freshness gate reads a negative age, and every coordinator
    looks unsynced. That is a real veto of the whole roster caused by nothing
    but the hour the board happened to start, and it is why this is clamped
    rather than left to a comment. It bit CI on the first Monday-morning run.
    """
    epoch = now.replace(hour=9, minute=0, second=0, microsecond=0)
    epoch -= timedelta(days=epoch.weekday())        # back to Monday
    return min(epoch, now)


class LabSession:
    """Thread-safe wrapper around one LabState plus its audit store."""

    def __init__(self, db_path: str = os.path.join("data", "visitboard.db")) -> None:
        self._lock = threading.RLock()
        self.db_path = db_path
        self.cfg: EngineConfig = load_config()
        self.matrix = ReliabilityMatrix.load()
        self.roster_config = Roster.load()
        self.reset()

    # -- lifecycle -----------------------------------------------------------

    def reload_settings(self) -> None:
        """Re-read the config files without rebuilding the lab.

        Deliberately not ``reset()``. A reset throws away the uploaded
        calendar, the assignments and the activity log, and somebody nudging a
        weight is not asking to lose the week's work -- if tweaking a number
        cost them the upload they would stop tweaking numbers, which defeats
        the point of the controls existing.

        So this re-reads the files and pushes the per-person values onto the
        coordinators already in play. Everything downstream -- scores, gates,
        rankings -- reads these on each call, so the next question the board is
        asked is answered under the new settings.
        """
        with self._lock:
            self.cfg = load_config()
            self.matrix = ReliabilityMatrix.load()
            self.roster_config = Roster.load()

            # Capacity is copied onto the Coordinator objects at build time, so
            # re-reading the roster alone would leave the old number in force
            # everywhere it actually gets used.
            by_id = self.roster_config.by_id()
            for cid, coord in self.state.coordinators.items():
                entry = by_id.get(cid)
                if entry is None:
                    continue
                coord.capacity_hours_week = entry.capacity_hours_week
                coord.van_trained = entry.van_trained
                coord.tech_trained = entry.tech_trained
                coord.out_of_hours_count = entry.out_of_hours_count

            # The lab's physical limits are cached for the length of a process,
            # because the gate consults them once per candidate per visit.
            from esd_scheduler import constraints
            constraints.clear_resource_cache()

            # Anything remembered about who needs a closer look was worked out
            # under the old numbers.
            self._attention_cache = {}

    def reset(self) -> None:
        with self._lock:
            # Two different clocks, and conflating them is what made the board
            # claim "synced 40 minutes ago" hours after it had actually synced.
            #
            #   epoch : start of this week. The synthetic lab is built against
            #           it so the demo's visits always land in the current week.
            #   now   : the real wall clock, read fresh every time. Evidence
            #           ages, protocol windows and the header all read from it.
            self.epoch = week_epoch(datetime.now())
            self.mode = board_mode()
            if self.mode == LIVE:
                self.state, visits = build_live(self.epoch)
            else:
                self.state, visits = build_lab(self.epoch)
            self.visits: Dict[str, object] = {v.visit_id: v for v in visits}
            self.order: List[str] = [v.visit_id for v in visits]
            self.provider = MockProvider(
                blocks=getattr(self.state, "demo_blocks", {}), clock=lambda: self.now
            )
            self.assignments: Dict[str, dict] = {}
            self.last_import: Optional[dict] = None
            self.availability: List[dict] = []
            self.resources: Dict[str, List[dict]] = {}
            self.calendar_roles: List[dict] = []
            self.unavailable: List[dict] = []
            self.unresolved_names: List[dict] = []
            self._import_fingerprint = None
            self._attention_cache: Dict[str, bool] = {}
            self.activity: List[dict] = []
            if getattr(self, "store", None):
                try:
                    self.store.close()
                except Exception:  # noqa: BLE001
                    pass
            self.store = AuditStore(self.db_path)
            self.store.record_config(self.cfg)
            # Calendars already read in an earlier run are evidence now, not
            # after the first screen refresh. Without this a fresh session
            # reports no calendar source while holding a database full of them.
            self._apply_confirmed_blocks()
            if self.mode == LIVE:
                restored = self._load_planned_visits()
                self._log(
                    f"Board reset. Live lab built from the roster; "
                    f"{restored} entered visit(s) restored.")
            else:
                self._log("Board reset. Synthetic lab rebuilt from the roster.")

    # -- calendar uploads ----------------------------------------------------

    def read_table(self) -> dict:
        """What the last upload actually read, one row per calendar in it.

        This is the table the page shows after Upload and Run, and the shape
        is taken from the print rather than invented: an Outlook export
        overlays several calendars, prints each in its own colour, and names
        them in the header. So a row is one overlaid calendar, and the two
        things a scheduler needs to check are whether the board worked out
        what that calendar *is* and, if it is a person, *which* person.

        Rows where it could not are the point of the exercise. They come back
        with ``needs_mapping`` set and an empty ``coordinator_id``, and the
        page offers the roster as a dropdown against them. Nothing on this
        board guesses a person from a colour.
        """
        last = self.last_import or {}
        blocks = {row[0]: row[1] for row in self.store.query(
            "SELECT coordinator_id, COUNT(*) FROM calendar_import_block "
            "WHERE reviewed=1 AND confirmed=1 GROUP BY coordinator_id")}
        hues = last.get("hues_seen") or {}
        legend = last.get("legend") or {}
        by_hue = {v: k for k, v in legend.items()} if isinstance(legend, dict) else {}

        # Who the board would actually schedule. Somebody can be on the print
        # and not on this list: `active: false` is how the lab takes a person
        # out of scheduling without deleting them. Their calendar is still
        # recognised as theirs -- so it never reads as an unidentified one a
        # scheduler has to go and map -- but its blocks are not read, because
        # busy time for somebody who can never be offered is time the board
        # would carry around and never use.
        scheduled = {e.id for e in self.roster_config.active}

        rows = []
        for entry in last.get("role_summary") or []:
            cid = entry.get("coordinator_id") or ""
            is_person = entry.get("role") == "coordinator"
            on_roster = bool(cid) and cid in scheduled and entry.get(
                "scheduled", True)
            meaning = entry.get("meaning", "")
            if is_person and cid and not on_roster:
                meaning = ("On this print, but not currently being "
                           "scheduled. The board recognises the calendar as "
                           "theirs so it is not mistaken for an unidentified "
                           "one; their blocks are not read, because they "
                           "will not be offered for a visit.")
            rows.append({
                "label": entry.get("label", ""),
                "role": entry.get("role", ""),
                "role_label": entry.get("role_label", ""),
                "polarity": entry.get("polarity", ""),
                "meaning": meaning,
                "coordinator_id": cid,
                # No count for somebody not being scheduled: their colour is
                # not attributed, so a 0 here would read as "free all week"
                # rather than "not read at all".
                "blocks": blocks.get(cid, 0) if cid and on_roster else None,
                "is_person": is_person,
                "scheduled": on_roster,
                # A person the lab has taken off scheduling is not an
                # unfinished decision. The board knows exactly whose calendar
                # it is; it just will not offer them.
                "needs_mapping": is_person and not cid,
            })

        # Colours the print used that belong to nobody the board could name.
        # These are the rows a person has to settle, so they lead the table.
        #
        # A hue is "unattributed" whenever it does not resolve to a person,
        # which includes every calendar that is not one: the export owner and
        # the lab's own room and shift calendars. Those already have a row
        # saying what they are, and asking somebody to map the ESDI lab room
        # to a coordinator would be asking the wrong question. Only colours
        # with no row at all are unfinished business.
        named = {r["label"] for r in rows}
        for hue in last.get("unattributed_hues") or []:
            label = by_hue.get(hue)
            if label and label in named:
                continue
            rows.append({
                "label": label or f"unnamed {hue} calendar",
                "role": "unknown", "role_label": "Not recognised",
                "polarity": "", "hue": hue,
                "meaning": ("This colour appears in the print but the board "
                            "could not tell whose calendar it is."),
                "coordinator_id": "", "blocks": None,
                "is_person": True, "scheduled": False, "needs_mapping": True,
            })

        rows.sort(key=lambda r: (not r["needs_mapping"], r["label"].lower()))
        return {
            "source_file": last.get("source_file"),
            "view_type": last.get("view_type"),
            "tier": last.get("tier"),
            "date_range": last.get("date_range"),
            "rows": rows,
            "needs_mapping": sum(1 for r in rows if r["needs_mapping"]),
            # Who a row can be pointed at. Read from the roster every time, so
            # adding a coordinator makes them selectable with no code change.
            #
            # Anyone already attributed on this print is included even when
            # they are not being scheduled, so their row shows the name the
            # board actually matched instead of an empty dropdown that reads
            # as "unknown". They are flagged, not hidden: the difference
            # between "we do not know who this is" and "we know, and we are
            # not offering them" is one a scheduler has to be able to see.
            "options": (
                [{"id": e.id, "name": e.name, "alias": alias_of(e),
                  "scheduled": True}
                 for e in self.roster_config.active]
                + [{"id": e.id, "name": e.name, "alias": alias_of(e),
                    "scheduled": False}
                   for e in self.roster_config.entries
                   if not e.active
                   and e.id in {r["coordinator_id"] for r in rows}]
            ),
            "hues_seen": hues,
        }

    def color_map_state(self) -> dict:
        """The hue -> person map, plus what this roster could match it to."""
        cmap = ColorMap.load()
        names = sorted({c.name for c in self.state.active_coordinators()})
        last = self.last_import or {}
        return {
            "confirmed": cmap.confirmed,
            "confirmed_by": cmap.confirmed_by,
            "confirmed_at": cmap.confirmed_at,
            "map": cmap.mapping,
            "hues_seen": last.get("hues_seen", {}),
            "calendar_names": last.get("calendar_names", []),
            "suggestions": {
                label: cid
                for label, cid in suggest_roster_matches(
                    last.get("calendar_names", []), self.state.coordinators
                ).items()
            },
            "roster": [
                {"coordinator_id": c.coordinator_id, "name": c.name}
                for c in sorted(
                    self.state.active_coordinators(), key=lambda c: c.coordinator_id
                )
            ],
            "roster_names": names,
        }

    def save_color_map(self, mapping: Dict[str, str], confirmed_by: str) -> dict:
        """Confirm the legend a human read off the live Outlook overlay.

        Requiring a name here is not ceremony: this map decides whose workload an
        entry lands on, and the PDF cannot check it.
        """
        known = {c.coordinator_id for c in self.state.active_coordinators()}
        clean = {
            str(hue): str(cid)
            for hue, cid in (mapping or {}).items()
            if cid and str(cid) in known
        }
        if not clean:
            raise ValueError("No colour was matched to a coordinator on the roster.")
        if not confirmed_by.strip():
            raise ValueError("Say who confirmed the colours; the PDF cannot verify them.")
        with self._lock:
            cmap = ColorMap(
                mapping=clean,
                confirmed=True,
                confirmed_by=confirmed_by.strip(),
                confirmed_at=datetime.now().isoformat(timespec="seconds"),
            )
            cmap.save()
            self._log(
                f"Calendar colours confirmed by {cmap.confirmed_by} "
                f"({len(clean)} of {len(self.state.coordinators)} coordinators mapped)."
            )
        return self.color_map_state()

    def upload_calendar_pdf(self, filename: str, blob: bytes,
                            image_hours: Optional[tuple] = None) -> dict:
        """Ingest an uploaded Outlook print and record it at its honest tier."""
        if not blob:
            raise ValueError("The upload was empty.")
        if len(blob) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"That file is {len(blob) // (1024 * 1024)} MB; the limit is "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            )
        is_pdf = blob.startswith(b"%PDF")
        is_pic = (blob.startswith(b"\x89PNG") or blob.startswith(b"\xff\xd8\xff")
                  or blob[:4] == b"GIF8" or blob[8:12] == b"WEBP")
        if not (is_pdf or is_pic):
            raise ValueError(
                "That is neither a PDF nor an image. Print the Outlook calendar "
                "to PDF, or take a screenshot of the calendar view.")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe = os.path.basename(filename or "calendar.pdf").replace("\\", "_")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(UPLOAD_DIR, f"{stamp}-{safe}")
        with open(path, "wb") as fh:
            fh.write(blob)

        with self._lock:
            result = import_pdf(
                path,
                coordinators=self.state.coordinators,
                now=datetime.now(),
                year_hint=self.now.year,
                # An image has no readable hour column without an OCR engine, so
                # the caller says what it covers. The defaults are the ordinary
                # Outlook print range and the current week, and whatever is used
                # is reported back rather than left implicit.
                image_hours=image_hours or (8.0, 18.0),
                image_start=self.epoch.date(),
            )
            self.store.record_import(result)
            payload = result.to_dict()
            payload["stored_as"] = os.path.basename(path)
            if result.view_type == "image":
                # Only call the range "assumed" when it actually was. If the
                # hour column was read, saying otherwise understates the result.
                payload["axis_source"] = result.axis_source
                if result.axis_source != "ocr":
                    payload["assumed_hours"] = list(image_hours or (8.0, 18.0))
            self.last_import = payload
            if result.availability:
                self.availability = result.availability
            if result.resources:
                self.resources = result.resources
            if result.roles:
                self.calendar_roles = result.to_dict()["role_summary"]
            if result.unavailable or result.unresolved_names:
                self.unavailable = result.unavailable
                self.unresolved_names = result.unresolved_names
            applied = self._apply_confirmed_blocks()
            payload["applied_blocks"] = applied
            who = ", ".join(
                a["name"] for a in result.availability if a.get("coordinator_id")
            ) or "nobody the roster recognises"
            if result.tier == TIER_MONTH_GRID:
                self._log(
                    f"Calendar upload {result.source_file}: month view, "
                    f"{result.entry_count} entries read for {who}."
                )
            else:
                self._log(
                    f"Calendar upload {result.source_file}: {result.view_type} view, "
                    f"{len(result.blocks)} blocks read and applied."
                )
        return payload

    # -- entered visits ------------------------------------------------------

    def _install_visit(self, row: dict) -> object:
        """Turn one stored row into a Family and a Visit on the live board."""
        fam = self.state.families.get(row["family_id"])
        if fam is None:
            fam = Family(family_id=row["family_id"], protocol=row["protocol"])
            self.state.families[row["family_id"]] = fam
        fam.protocol = row["protocol"]
        fam.zone = int(row.get("zone") or 1)
        if row.get("participant_status"):
            fam.participant_status = row["participant_status"]
        for field_name in ("birth_date", "due_date"):
            if row.get(field_name):
                try:
                    setattr(fam, field_name, date.fromisoformat(row[field_name]))
                except ValueError:
                    pass          # an unreadable date is no date, not a crash
        fam.passed_to_retention = bool(row.get("passed_to_retention"))
        if row.get("anchor_date"):
            fam.anchor_date = date.fromisoformat(row["anchor_date"])
        if row.get("drive_time_minutes") is not None:
            fam.drive_time_minutes = float(row["drive_time_minutes"])
        if row.get("contact_method"):
            fam.preferred_contact_method = row["contact_method"]
        if row.get("notes"):
            fam.scheduling_notes = row["notes"]

        self._seed_history(fam, row.get("completed_through"))

        visit = Visit(
            visit_id=row["visit_id"],
            family_id=row["family_id"],
            protocol=row["protocol"],
            checkpoint=row["checkpoint"],
            window_start=datetime.fromisoformat(row["window_start"]),
            window_end=datetime.fromisoformat(row["window_end"]),
            duration_hours=float(row["duration_hours"]),
            location=row.get("location") or "lab",
            requires_clinician=bool(row.get("requires_clinician")),
        )
        self.visits[visit.visit_id] = visit
        if visit.visit_id not in self.order:
            self.order.append(visit.visit_id)
        return visit

    def _seed_history(self, fam, completed_through: Optional[str]) -> None:
        """Record the checkpoints a family has already been through.

        Without this the protocol clock sees a family with no history at all
        and reports their very first checkpoint as months overdue, however
        recently they were actually seen. Entering "we have done up to 3m" is
        the smallest thing a scheduler can say that makes the clock right.

        The coordinator on these rows is deliberately blank. Who ran a past
        visit is what the continuity term rewards, and inventing a name there
        would hand somebody credit for a visit nobody recorded. Blank means
        the board knows the visit happened and not who did it, which is true.
        """
        if not completed_through:
            return
        checkpoints = ProtocolSchedule.load().for_protocol(fam.protocol)
        names = [c.name for c in checkpoints]
        if completed_through not in names:
            return
        cutoff = names.index(completed_through)
        already = {h.checkpoint for h in self.state.history
                   if h.family_id == fam.family_id}
        for cp in checkpoints[:cutoff + 1]:
            if cp.name in already:
                continue
            when = self.now
            if fam.anchor_date:
                when = datetime.combine(
                    fam.anchor_date + timedelta(days=cp.offset_days), time(9, 0))
            self.state.history.append(CompletedVisit(
                visit_id=f"H-{fam.family_id}-{cp.name}",
                family_id=fam.family_id,
                coordinator_id="",
                when=when,
                protocol=fam.protocol,
                checkpoint=cp.name,
                duration_hours=cp.duration_hours,
            ))

    def _load_planned_visits(self) -> int:
        """Put entered visits back after a restart."""
        rows = [dict(r) for r in self.store.planned_visits()]
        for row in rows:
            try:
                self._install_visit(row)
            except (KeyError, TypeError, ValueError):
                continue        # a row this build cannot read is skipped, not fatal
        self._reorder_by_window()
        return len(rows)

    def _reorder_by_window(self) -> None:
        """Soonest window first, which is the order a scheduler works in."""
        self.order.sort(key=lambda vid: (
            getattr(self.visits[vid], "window_start", self.now), vid))

    def add_visit(self, payload: dict) -> dict:
        """Enter a real visit. Returns the visit as the board now holds it."""
        with self._lock:
            required = ("family_id", "protocol", "checkpoint",
                        "window_start", "window_end")
            missing = [k for k in required if not str(payload.get(k) or "").strip()]
            if missing:
                raise ValueError("Missing: " + ", ".join(missing))
            try:
                start = datetime.fromisoformat(str(payload["window_start"]))
                end = datetime.fromisoformat(str(payload["window_end"]))
            except ValueError as exc:
                raise ValueError(f"Could not read the window dates: {exc}") from exc
            if end < start:
                raise ValueError("The window ends before it starts.")

            protocol = str(payload["protocol"]).strip().upper()
            checkpoint = str(payload["checkpoint"]).strip()
            # The manual's visit-length table is already in the protocol
            # schedule, so a visit entered without a length takes the one the
            # protocol states rather than a flat two hours.
            planned = next(
                (c for c in ProtocolSchedule.load().for_protocol(protocol)
                 if c.name == checkpoint), None)
            default_hours = (planned.duration_hours
                             if planned and planned.duration_hours else 2.0)

            # A participant ID is how every other system in the lab refers to
            # this child -- Access, the calendar invite title, the visit folder.
            # Catching a typo here costs a second; catching it on the doorstep
            # costs a visit. The F prefix is tolerated because the board's own
            # demo writes them that way, and normalised to the manual's form.
            status = str(payload.get("visit_status") or "Future").strip()
            if status not in VISIT_STATUSES:
                raise ValueError(
                    f"{status!r} is not a visit status. Access offers: "
                    + ", ".join(VISIT_STATUSES))

            family_id = str(payload["family_id"]).strip().lstrip("Ff#").strip()
            rule = ProtocolSchedule.load().family_id_rule(protocol)
            if rule and not re.fullmatch(rule[0], family_id):
                raise ValueError(
                    f"{family_id!r} is not a {protocol} participant ID. "
                    f"Expected {rule[1]}.")

            vid = str(payload.get("visit_id") or "").strip() or self._next_visit_id()
            if vid in self.visits:
                raise ValueError(f"Visit {vid} is already on the board.")
            row = {
                "visit_id": vid,
                "family_id": family_id,
                "protocol": protocol,
                "checkpoint": checkpoint,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "duration_hours": float(payload.get("duration_hours")
                                        or default_hours),
                "location": str(payload.get("location") or "lab"),
                "requires_clinician": 1 if payload.get("requires_clinician") else 0,
                "anchor_date": (str(payload["anchor_date"])
                                if payload.get("anchor_date") else None),
                "zone": int(payload.get("zone") or 1),
                "drive_time_minutes": (float(payload["drive_time_minutes"])
                                       if payload.get("drive_time_minutes") not in
                                       (None, "") else None),
                "contact_method": payload.get("contact_method") or None,
                "notes": payload.get("notes") or None,
                "completed_through": payload.get("completed_through") or None,
                "visit_status": status,
                "participant_status": (str(payload.get("participant_status") or "")
                                       .strip().upper() or None),
                "birth_date": str(payload["birth_date"]) if payload.get("birth_date") else None,
                "due_date": str(payload["due_date"]) if payload.get("due_date") else None,
                "passed_to_retention": 1 if payload.get("passed_to_retention") else 0,
                "created_at": self.now.isoformat(timespec="seconds"),
            }
            self.store.add_planned_visit(row)
            visit = self._install_visit(row)
            self._reorder_by_window()
            self._log(f"Visit {vid} entered for family {row['family_id']}: "
                      f"{row['protocol']} {row['checkpoint']}.")
            return {"visit_id": visit.visit_id, "family_id": visit.family_id,
                    "protocol": visit.protocol, "checkpoint": visit.checkpoint,
                    "window_start": visit.window_start.isoformat(),
                    "window_end": visit.window_end.isoformat()}

    def remove_visit(self, visit_id: str) -> bool:
        """Take an entered visit off the board."""
        with self._lock:
            gone = self.store.remove_planned_visit(visit_id)
            self.visits.pop(visit_id, None)
            if visit_id in self.order:
                self.order.remove(visit_id)
            self.assignments.pop(visit_id, None)
            if gone:
                self._log(f"Visit {visit_id} removed from the board.")
            return gone

    def _next_visit_id(self) -> str:
        """V001, V002, ... skipping anything already taken."""
        n = 1
        while f"V{n:03d}" in self.visits:
            n += 1
        return f"V{n:03d}"

    def _covered_ranges(self):
        """The dates each coordinator's uploads claim to describe.

        Read from the printed range on each import rather than from the blocks
        themselves. A week with meetings only on Monday still covers Friday,
        and inferring coverage from where the blocks happen to fall would call
        an empty Friday "not covered" and hold somebody back for no reason.
        """
        from datetime import date as _date
        spans = {}
        rows = self.store.query(
            "SELECT b.coordinator_id, i.date_range "
            "FROM calendar_import_block b "
            "JOIN calendar_import i ON i.import_id = b.import_id "
            "WHERE b.reviewed=1 AND b.confirmed=1")
        for cid, printed in rows:
            if not printed or " to " not in printed:
                continue
            try:
                lo, hi = [_date.fromisoformat(p.strip())
                          for p in printed.split(" to ")]
            except ValueError:
                continue
            if cid in spans:
                have_lo, have_hi = spans[cid]
                spans[cid] = (min(have_lo, lo), max(have_hi, hi))
            else:
                spans[cid] = (lo, hi)
        return spans

    def _apply_confirmed_blocks(self) -> int:
        """Push reviewed-and-confirmed blocks into the live snapshots.

        Only confirmed blocks land. An unreviewed parse is not evidence, so it
        must not narrow anybody's availability while it waits for a human.
        """
        rows = [dict(r) for r in self.store.confirmed_blocks()]
        # The dates each coordinator's uploads actually printed, so a snapshot
        # can say what it does and does not cover.
        covered = self._covered_ranges()
        by_coord: Dict[str, List[BusyBlock]] = {}
        for row in rows:
            try:
                start = datetime.fromisoformat(row["start_ts"])
                end = datetime.fromisoformat(row["end_ts"])
            except (TypeError, ValueError):
                continue
            by_coord.setdefault(row["coordinator_id"], []).append(
                BusyBlock(start=start, end=end, status="busy")
            )
        applied = 0
        for cid, blocks in by_coord.items():
            snap = self.state.calendars.get(cid)
            if snap is None:
                if cid not in self.state.coordinators:
                    continue        # a block for somebody not on the roster
                # A live board starts with no snapshots at all, because an
                # absent one reads as expired and therefore as unknown rather
                # than free. Reading somebody's calendar is exactly the moment
                # they stop being unknown, so the snapshot is created here.
                snap = CalendarSnapshot(
                    coordinator_id=cid, provider="manual", fetched_at=self.now)
                self.state.calendars[cid] = snap
            keep = [b for b in snap.blocks if getattr(b, "source", None) != "pdf_import"]
            snap.blocks = keep + blocks
            # Stamp with the board's own clock, not the wall clock. The demo lab
            # is anchored to the start of the week, so a wall-clock stamp reads
            # as evidence from the future and the freshness gate goes negative.
            snap.fetched_at = self.now
            # Narrow the snapshot to the dates the print covered, but only when
            # the print is the whole story. A demo board also holds mock blocks
            # that do describe the current week, and clipping those to an
            # uploaded fixture's dates would make the demo unstaffable for a
            # week the provider knows perfectly well.
            span = covered.get(cid)
            if span and not keep:
                snap.covers_from, snap.covers_to = span
            applied += len(blocks)
        return applied

    def refresh_calendar(self) -> bool:
        """Pick up imports made outside this process, e.g. by the inbox job.

        The board is long-running and is not the only writer: a scheduled job
        drops calendar imports into the same audit store. Without this, a PDF
        filed by automation would sit in the database and never reach the
        screen until someone restarted the server.
        """
        with self._lock:
            fingerprint = self.store.import_fingerprint()
            if fingerprint == self._import_fingerprint:
                return False
            self._import_fingerprint = fingerprint

            payload = self.store.latest_import()
            if payload:
                self.last_import = payload
                if payload.get("availability"):
                    self.availability = payload["availability"]
                if payload.get("resources"):
                    self.resources = payload["resources"]
                if payload.get("role_summary"):
                    self.calendar_roles = payload["role_summary"]
                self.unavailable = payload.get("unavailable", []) or []
                self.unresolved_names = payload.get("unresolved_names", []) or []
            self._apply_confirmed_blocks()
            return True

    def imports(self) -> dict:
        """Upload history, plus the blocks still waiting on a human."""
        self.refresh_calendar()
        rows = [dict(r) for r in self.store.imports(limit=15)]
        for row in rows:
            row["blockers"] = _loads(row.get("blockers"))
            row["notes"] = _loads(row.get("notes"))
            row["schedulable"] = bool(row.get("schedulable"))
            row["tier_rule"] = TIER_RULES.get(row.get("tier"), "")
            row.pop("payload", None)
        pending = [
            {
                "block_id": b["block_id"],
                "coordinator_id": b["coordinator_id"],
                "coordinator": self._person_label(b["coordinator_id"]),
                "start": b["start_ts"],
                "end": b["end_ts"],
                "import_id": b["import_id"],
            }
            for b in (dict(r) for r in self.store.import_blocks())
            if not b["reviewed"]
        ]
        confirmed = len(self.store.confirmed_blocks())
        return {
            "imports": rows,
            "pending_review": pending,
            "confirmed_blocks": confirmed,
            "last_import": self.last_import,
            "color_map": self.color_map_state(),
            "availability": self.availability,
            "availability_month": self._availability_month(),
            "resources": self.resources,
            "roles": self.calendar_roles,
            "filters": self.filter_state(),
            "unavailable": self.unavailable,
            "unresolved_names": self.unresolved_names,
            "applied": [
                {
                    "block_id": b["block_id"],
                    "coordinator": getattr(
                        self.state.coordinators.get(b["coordinator_id"]), "name",
                        b["coordinator_id"]),
                    "start": b["start_ts"],
                    "end": b["end_ts"],
                }
                for b in (dict(r) for r in self.store.confirmed_blocks())
            ][:60],
        }

    def coordinator_table(self) -> dict:
        """One row per coordinator: their week, and what they can take on.

        Everything here answers a question a scheduler asks out loud. What a
        person scored on four weighted criteria is not one of those questions,
        so it is not here; it belongs with the visit being decided.
        """
        now = self.now
        monday = self.epoch.date()
        by_id = self.roster_config.by_id()
        grid = week_grid(self.state, monday, now, slot_minutes=60)
        certs = {r["coordinator_id"]: r for r in self.certifications()["rows"]}

        rows = []
        for coord in sorted(self.state.active_coordinators(), key=lambda c: c.name):
            cid = coord.coordinator_id
            days = []
            free_hours = 0
            for day in grid:
                # free_ids is the only list carrying ids. Anything not in it is
                # busy or unverified, and both mean the same thing here: do not
                # offer this hour.
                slots = [
                    {"label": s["label"],
                     "state": "free" if cid in s["free_ids"] else "busy"}
                    for s in day["slots"]
                ]
                free = sum(1 for s in slots if s["state"] == "free")
                free_hours += free
                days.append({"label": day["label"].split()[0],
                             "date": day["day"], "slots": slots, "free": free})

            snapshot = self.state.calendars.get(cid)
            cert = certs.get(cid, {})
            entry = by_id.get(cid)
            rows.append({
                "id": cid,
                "name": coord.name,
                "alias": alias_of(entry),
                "initials": "".join(p[0] for p in coord.name.split()[:2]).upper(),
                "days": days,
                "free_hours": free_hours,
                "visits_this_week": self.state.visits_this_week(cid, now),
                "van_trained": bool(coord.van_trained),
                "in_lab_day": coord.in_lab_day,
                "can_run": cert.get("reliable", []),
                "learning": cert.get("training", []),
                "is_clinician": bool(cert.get("reliable")),
                "calendar_ok": bool(snapshot and snapshot.sync_ok),
                # What the roster says this person is, and the visit ages they
                # can run alone. Shown because "signed off on CSBS" and "can be
                # the clinician on a 9m visit" are different facts and the
                # board was only ever showing the first.
                "roles": list(getattr(entry, "roles", []) or []),
                "solo_range": (f"{entry.solo_from}-{entry.solo_to}"
                               if getattr(entry, "solo_from", None)
                               and getattr(entry, "solo_to", None) else None),
                "confirm_first": bool(getattr(entry, "confirm_before_offering", False)),
            })
        return {
            "rows": rows,
            "days": [d["label"].split()[0] for d in grid],
            "dates": [d["day"] for d in grid],
            "week_of": monday.isoformat(),
        }

    def certifications(self) -> dict:
        """The reliability chart, as the board reads it.

        Recency is reported as unknown rather than computed. The manual's chart
        states current status only and carries no dates, so any decay curve here
        would be arithmetic on a number nobody recorded.
        """
        m = self.matrix
        labels = getattr(m, "labels", None) or {}
        try:
            import json as _json
            with open(os.path.join("config", "reliability-matrix.json"),
                      encoding="utf-8") as fh:
                labels = _json.load(fh).get("_assessment_labels", {}) or {}
        except (OSError, ValueError):
            labels = {}

        rows = []
        for coord in sorted(self.state.active_coordinators(), key=lambda c: c.name):
            cid = coord.coordinator_id
            reliable = [a for a in m.assessments if m.is_reliable(cid, a)]
            training = [a for a in m.assessments if m.is_in_training(cid, a)]
            rows.append({
                "coordinator_id": cid,
                "name": coord.name,
                "reliable": [labels.get(a, a) for a in reliable],
                "training": [labels.get(a, a) for a in training],
                "n_reliable": len(reliable),
                "clinician": bool(reliable),
                "recency": None,
            })
        return {
            "rows": rows,
            "assessments": [labels.get(a, a) for a in m.assessments],
            "confirmed": m.confirmed,
            "recency_recorded": False,
            "requirements": {
                p: reqs for p, reqs in m.requirements.items()
                if not p.startswith("_")
            },
        }

    def availability_grid(self, slot_minutes: int = 30) -> dict:
        """Who is free in each slot this week, plus whose calendar is missing.

        Coverage is reported beside the grid rather than under it. A grid built
        from four of seven calendars looks complete and is not: the three the
        board has not seen show as unavailable everywhere, which reads as a busy
        team rather than a partial sync.
        """
        self.keep_calendars_fresh()
        with self._lock:
            now = self.now
            return {
                "week": week_grid(self.state, self.epoch.date(), now,
                                  slot_minutes=slot_minutes),
                "coverage": coverage_report(self.state, now),
                "slot_minutes": slot_minutes,
            }

    def logic(self) -> dict:
        """The decision procedure, described from the live configuration.

        Everything here is read from the same objects the engine uses, so the
        explanation cannot drift from the behaviour. A diagram maintained by
        hand would be wrong the first time a weight changed.
        """
        from esd_scheduler.scenarios import GATE_LABEL, GATE_ORDER

        w = self.cfg.weights.as_dict()
        return {
            "weights": [
                {"key": k, "label": CRITERION_LABEL.get(k, k), "weight": round(v, 3)}
                for k, v in w.items()
            ],
            "weight_total": round(sum(w.values()), 3),
            "gates": [
                {"order": i + 1, "key": g,
                 "label": GATE_LABEL.get(g, (g, ""))[0],
                 "why": GATE_LABEL.get(g, (g, ""))[1]}
                for i, g in enumerate(GATE_ORDER)
            ],
            "review_band": round(self.cfg.epsilon_review_band, 3),
            "gamma_travel": round(self.cfg.gamma_travel, 3),
            "certifications": self.certifications(),
            "weight_vector_id": self.cfg.vector_id(),
            "priority_tiers": list(self.PRIORITY_TIERS),
        }

    def schedule_rows(self) -> dict:
        """Which family is owed a visit next, and how late it is.

        Separate axis from the coordinator ranking: this says *which visit* is
        pressing, that says *who should take it*. Keeping them apart means an
        urgent visit never quietly promotes an ineligible coordinator.
        """
        sched = ProtocolSchedule.load()
        rows = [r.to_dict() for r in upcoming(self.state.families,
                                              self.state.history, self.now, sched)]
        counts = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {
            "rows": rows,
            "counts": counts,
            "confirmed": sched.confirmed,
            "confirmed_by": sched.confirmed_by,
            "source": sched.source,
            "actionable": sum(counts.get(k, 0)
                              for k in ("overdue", "closing", "open")),
        }

    def filter_state(self) -> List[dict]:
        """The policy calendars in force, and how much they cover.

        A filter with no windows is reported as inactive rather than hidden: an
        empty offered-times calendar and an absent one look identical on screen
        otherwise, and they mean very different things.
        """
        out = []
        for role in ("offered_window", "clinician_shift", "lab_space"):
            windows = self.resources.get(role, [])
            hours = 0.0
            for w in windows:
                try:
                    hours += (
                        datetime.fromisoformat(w["end"])
                        - datetime.fromisoformat(w["start"])
                    ).total_seconds() / 3600.0
                except (TypeError, ValueError, KeyError):
                    continue
            out.append({
                "role": role,
                "label": ROLE_LABEL.get(role, role),
                "meaning": ROLE_MEANING.get(role, ""),
                "polarity": POLARITY.get(role, "ignored"),
                "windows": len(windows),
                "hours": round(hours, 1),
                "active": bool(windows),
            })
        return out

    def visit_filters(self, visit) -> List[dict]:
        """How one visit fares against each policy calendar."""
        start = visit.window_start
        end = start + timedelta(hours=visit.duration_hours)
        checks = resource_checks(
            start, end, self.resources,
            requires_clinician=getattr(visit, "requires_clinician", False),
            in_lab=getattr(visit, "location", "lab") == "lab",
        )
        return [
            {
                "role": role,
                "label": ROLE_LABEL.get(role, role),
                "state": state,
                "why": why,
            }
            for role, (state, why) in checks.items()
        ]

    def _availability_month(self) -> str:
        """The month the current availability grid covers, for the heading."""
        days = [d["day"] for a in self.availability for d in a.get("days", [])]
        if not days:
            return ""
        mid = sorted(days)[len(days) // 2]
        try:
            return datetime.fromisoformat(mid).strftime("%B %Y")
        except ValueError:
            return ""

    def review_all_pending(self, confirmed: bool, reviewer: str) -> dict:
        """Settle everything still waiting.

        Offered because reviewing fifty blocks one at a time is not review, it
        is clicking; someone checking a screenshot against the board wants to
        accept the run and pick off the wrong ones. Which way round it went is
        recorded against the reviewer either way.
        """
        with self._lock:
            n = self.store.review_pending(confirmed, reviewer or "coordinator")
            applied = self._apply_confirmed_blocks()
            self._log(
                f"{n} block(s) {'confirmed' if confirmed else 'rejected'} in one pass."
            )
        out = self.imports()
        out["applied_blocks"] = applied
        out["settled"] = n
        return out

    def _person_label(self, cid: str) -> str:
        """A name for a coordinator id, even one the board is not scheduling.

        ``state.coordinators`` holds only active people, so an id belonging to
        somebody taken off scheduling fell through to the raw id -- a review
        queue asking a scheduler to confirm time for "C06" tells them nothing
        and looks like corrupt data. The roster still knows who that is, and
        saying they are not being scheduled is the useful part: it is why
        their block is worth rejecting.
        """
        coord = self.state.coordinators.get(cid)
        if coord is not None:
            return getattr(coord, "name", cid)
        entry = self.roster_config.by_id().get(cid)
        if entry is not None:
            return f"{entry.name} (not scheduled)"
        return cid

    def review_import_block(self, block_id: str, confirmed: bool, reviewer: str) -> dict:
        with self._lock:
            if not self.store.review_block(block_id, confirmed, reviewer or "coordinator"):
                raise KeyError(f"No such block: {block_id}")
            applied = self._apply_confirmed_blocks()
            self._log(
                f"Calendar block {'confirmed' if confirmed else 'rejected'} on review."
            )
        out = self.imports()
        out["applied_blocks"] = applied
        return out

    @property
    def now(self) -> datetime:
        """The real time, read fresh on every access.

        Never cached. A board left open overnight has to know that, and a value
        frozen at startup silently turns every freshness figure into a fiction.
        """
        return datetime.now()

    def keep_calendars_fresh(self) -> bool:
        """Re-pull the mock calendars on the same cadence as the real job.

        In production a launchd agent syncs every five minutes; without an
        equivalent here the demo's evidence would age past the staleness
        threshold within the hour and veto the entire roster for a reason that
        is an artefact of nobody running the job.
        """
        if not isinstance(self.provider, MockProvider):
            return False
        now = self.now
        oldest = min(
            (c.fetched_at for c in self.state.calendars.values()), default=None)
        if oldest is not None and (now - oldest).total_seconds() < 300:
            return False
        with self._lock:
            for snapshot in self.state.calendars.values():
                snapshot.fetched_at = now
                snapshot.sync_ok = True
        return True

    def _log(self, message: str) -> None:
        self.activity.insert(
            0, {"at": datetime.now().strftime("%H:%M"), "message": message}
        )
        del self.activity[40:]

    # -- reads ---------------------------------------------------------------

    def calendar_source(self) -> str:
        """Where the busy time on this board actually came from.

        Reported rather than assumed, because "demo" printed next to real
        uploaded calendars is the kind of label that gets believed. A live
        board says ``none`` until a calendar has been read, and ``upload``
        once one has.
        """
        if self.mode == DEMO:
            return "demo"
        read = any(c.blocks for c in self.state.calendars.values())
        return "upload" if read else "none"

    def health(self) -> dict:
        return {
            "ok": True,
            "engine_version": ENGINE_VERSION,
            "weight_vector_id": self.cfg.vector_id(),
            "config_fingerprint": self.cfg.fingerprint(),
            "review_band": round(self.cfg.epsilon_review_band, 3),
            "review_band_calibrated": self.cfg.epsilon_calibrated,
            "graph_auth_mode": self.cfg.graph_auth_mode,
            "calendar_source": self.calendar_source(),
            "mode": self.mode,
            "reads_titles": False,
            "weights": self.cfg.weights.as_dict(),
            "week_of": self.epoch.strftime("%Y-%m-%d"),
            "server_time": self.now.isoformat(timespec="seconds"),
            # Master §4: a scheduler making an assignment needs to know how
            # current the evidence is without navigating away.
            "last_synced_iso": min(
                (c.fetched_at for c in self.state.calendars.values()), default=self.now
            ).isoformat(timespec="seconds"),
            "last_synced_minutes": round(max(
                (self.now - c.fetched_at).total_seconds() / 60
                for c in self.state.calendars.values()
            ) if self.state.calendars else 0),
            "reliability_matrix_confirmed": self.matrix.confirmed,
            "ndd_certified_count": len(self.matrix.ndd_certified()),
        }

    def roster(self) -> List[dict]:
        with self._lock:
            out = []
            for c in sorted(
                self.state.active_coordinators(), key=lambda c: c.coordinator_id
            ):
                committed = self.state.committed(c.coordinator_id)
                capacity = max(1e-6, c.capacity_hours_week)
                # Contract capacity is what a person is paid for; effective
                # capacity is what the engine actually scores against, reduced
                # during onboarding by the capacity ramp. Both are exported: the
                # first is what a coordinator recognises, the second is what the
                # burden term used, and confusing them silently changes scores.
                effective = ramped_capacity(c, self.cfg)
                out.append(
                    {
                        "id": c.coordinator_id,
                        "name": c.name,
                        "initials": "".join(p[0] for p in c.name.split()[:2]).upper(),
                        "credentials": sorted(c.credentials),
                        "capacity_hours": round(c.capacity_hours_week, 1),
                        "effective_capacity_hours": round(effective, 3),
                        "committed_hours": round(committed, 1),
                        "utilization": round(min(1.5, committed / capacity), 3),
                        "visits_this_week": self.state.visits_this_week(
                            c.coordinator_id, self.now
                        ),
                        "is_new": c.n_completed_visits < self.cfg.n_min_visits,
                        # Master §7 staff fields, surfaced for the equity view.
                        "van_trained": c.van_trained,
                        "tech_trained": c.tech_trained,
                        "out_of_hours": c.out_of_hours_count,
                        "in_lab_day": c.in_lab_day,
                        # Evidence freshness, per coordinator (Master §4).
                        "evidence_age_minutes": (
                            round((self.now - snap.fetched_at).total_seconds() / 60)
                            if (snap := self.state.calendars.get(c.coordinator_id))
                            and snap.sync_ok else None
                        ),
                        "blocks_total": len(snap.blocks) if snap else 0,
                        "blocks_reviewed": len(snap.blocks) if snap else 0,
                    }
                )
            return out

    def visit_summary(self, visit_id: str) -> dict:
        v = self.visits[visit_id]
        family = self.state.families[v.family_id]
        routing = route_visit(v, family, self.matrix)
        assigned = self.assignments.get(visit_id)
        return {
            "id": v.visit_id,
            "family_id": v.family_id,
            "protocol": v.protocol,
            "checkpoint": v.checkpoint,
            "title": f"{v.checkpoint} {v.protocol} visit",
            # Strip a leading F if the id carries one, rather than dropping the
            # first character whatever it is. The demo writes F5031 and the lab
            # writes 5031; blind slicing turned the second into "Family 031",
            # which is a different participant to go looking for.
            "family_label": f"Family {v.family_id.lstrip('Ff')}",
            "date": v.window_start.strftime("%Y-%m-%d"),
            "day_label": v.window_start.strftime("%a %-d %b"),
            "window": (
                f"{v.window_start:%a %-d %b}, {v.window_start:%-I:%M %p}"
                f" to {v.window_end:%-I:%M %p}"
                if v.window_start.date() == v.window_end.date()
                else f"{v.window_start:%a %-d %b} to {v.window_end:%a %-d %b}"
            ),
            "status": "assigned" if assigned else "needs_assignment",
            "needs_attention": self._needs_attention(visit_id),
            # Master §6: these belong beside the ranked coordinators, not in a
            # settings page. The scheduler is about to contact this family.
            "preferred_contact_method": family.preferred_contact_method,
            "scheduling_notes": family.scheduling_notes,
            "is_ndd": family.is_ndd_cross_collab,
            "drive_time_minutes": round(family.drive_time_minutes),
            "duration_hours": visit_duration_hours(v, family),
            "route": routing.route,
            "automated": routing.automated,
            "route_reason": routing.reason,
            "escalate": routing.escalate,
            "offer_window": [round(x, 2) for x in offer_window(v, family)],
            "location": getattr(v, "location", "lab"),
            "requires_clinician": getattr(v, "requires_clinician", False),
            "filters": self.visit_filters(v),
            "assigned_to": assigned["coordinator_name"] if assigned else None,
            "assigned_id": assigned["coordinator_id"] if assigned else None,
            "provisional": bool(assigned and assigned.get("provisional")),
            "was_override": bool(assigned and assigned.get("override")),
        }

    def _needs_attention(self, visit_id: str) -> bool:
        """True when this visit will not resolve itself.

        Either nobody can go, or the top two are so close that picking between
        them is a judgement call rather than a ranking. These are the only
        visits worth pulling someone's eye toward, so they are the only ones the
        header counts.
        """
        if visit_id in self.assignments:
            return False
        cached = self._attention_cache.get(visit_id)
        if cached is not None:
            return cached
        visit = self.visits[visit_id]
        pool = score_visit(visit, self.state, self.cfg, self.now)
        assignable = [
            c for c in pool.candidates
            if not fairness_violations(c.coordinator_id, c, self.state, self.cfg, self.now)
        ]
        flag = (not assignable) or (
            len(pool.candidates) >= 2 and pool.candidates[0].review_band_flag
        )
        self._attention_cache[visit_id] = flag
        return flag

    # Priority tiers, most pressing first. Deliberately lexicographic rather
    # than a weighted blend: a blend needs coefficients trading "two weeks late"
    # against "needs a closer look", and nobody has justified those numbers.
    # Tiers say what the lab already believes -- deal with the late ones first,
    # and never let an unassigned visit sink below an assigned one.
    PRIORITY_TIERS = ("overdue", "closing", "open", "upcoming", "unknown")

    def queue(self) -> List[dict]:
        with self._lock:
            sched = {r.family_id: r for r in upcoming(
                self.state.families, self.state.history, self.now)}
            rows = []
            for vid in self.order:
                row = self.visit_summary(vid)
                due = sched.get(row["family_id"])
                status = due.status if due else "unknown"
                row["due_status"] = status
                row["due_label"] = STATUS_LABEL.get(status, status)
                row["urgency"] = round(due.urgency, 3) if due else 0.0
                row["days_remaining"] = due.days_remaining if due else None
                try:
                    tier = self.PRIORITY_TIERS.index(status)
                except ValueError:
                    tier = len(self.PRIORITY_TIERS)
                # Assigned work drops below everything still open, whatever its
                # window: it is done being decided.
                # Days remaining, ascending, is the tiebreak inside a tier.
                # Urgency saturates at 1.0 the moment a window closes, so
                # ranking on it alone puts a fortnight late and three months
                # late on the same footing; the raw day count keeps ordering
                # them. A family with no anchor sorts last rather than first,
                # which is what a missing value would otherwise do.
                row["priority"] = (
                    (1 if row["status"] == "assigned" else 0),
                    tier,
                    row["days_remaining"] if row["days_remaining"] is not None
                    else 10 ** 6,
                    row["date"],
                )
                rows.append(row)
            rows.sort(key=lambda r: r["priority"])
            for row in rows:
                row["priority"] = list(row["priority"])
            return rows

    def _apply_gates(self, visit, family, pool):
        """Run the Layer 1 gates over a scored pool. One implementation.

        Both the screen and the assignment path need this, and they have to
        agree: when only the screen applied it, the board showed a coordinator
        as ineligible and then accepted an assignment to them, and demanded an
        override reason for its own recommendation because the two paths
        disagreed about who the recommendation was.

        Anyone a gate rejects leaves the ranking entirely. A hard rule is not
        something a good score is allowed to argue with.
        """
        survivors, gated = [], []
        roster = [
            self.state.coordinators[c.coordinator_id]
            for c in pool.candidates
            if c.coordinator_id in self.state.coordinators
        ]
        for cand in pool.candidates:
            coord = self.state.coordinators.get(cand.coordinator_id)
            if coord is None:
                survivors.append(cand)
                continue
            slot = None
            if cand.feasibility.slot_start and cand.feasibility.slot_end:
                slot = (cand.feasibility.slot_start, cand.feasibility.slot_end)
            verdict = check_candidate(
                coord, visit, family, self.state, self.now, slot=slot,
                matrix=self.matrix, pool=roster,
            )
            if verdict.passed:
                survivors.append(cand)
            else:
                gated.append({
                    "id": cand.coordinator_id,
                    "name": cand.coordinator_name,
                    "reason": verdict.reason or verdict.gate,
                })
        return survivors, gated

    def is_remote(self, visit) -> bool:
        """Whether this checkpoint is ever seen in person.

        The manual is explicit for NANO 24m: "we do not see participants for an
        in-person visit". It is questionnaires and, only if the child flags, a
        clinical phone call. Ranking staff for one is not a small cosmetic
        error -- it puts two people and a vehicle against a visit that nobody
        attends, and it consumes one of the two NANO tech kits on the board.
        """
        planned = next(
            (c for c in ProtocolSchedule.load().for_protocol(visit.protocol)
             if c.name == visit.checkpoint), None)
        return bool(planned and planned.remote)

    def _profile_config(self):
        """Visit profiles and eligibility rules, read once per board."""
        if getattr(self, "_profiles", None) is None:
            self._profiles = eligibility.ProfileConfig.load()
        return self._profiles

    def eligible_for(self, visit):
        """Who may staff this visit at all, before anybody is scored.

        Being free, being owed a visit, or having seen the family before
        cannot make somebody able to run an assessment they are not signed off
        on. So this decides the pool and the weighted score only orders what
        survives it.
        """
        return eligibility.evaluate(
            visit, list(self.state.coordinators), self.roster_config,
            self.matrix, self.now, self._profile_config())

    def _eligible_pool(self, visit, pool):
        """Drop anyone the eligibility layer refuses, before ranking.

        Shared by the screen and the assignment path on purpose. When only one
        of them filtered, the board showed a person as ineligible and then
        accepted an assignment to them, which is the same class of bug the
        Layer 1 gates had before they were given one implementation.
        """
        elig = self.eligible_for(visit)
        if elig.profile_id and not elig.remote:
            allowed = set(elig.eligible_ids)
            pool.candidates = [c for c in pool.candidates
                               if c.coordinator_id in allowed]
        return pool, elig

    def candidates(self, visit_id: str) -> dict:
        """The full ranked pool for one visit, in the words the screen needs."""
        with self._lock:
            visit = self.visits[visit_id]
            family = self.state.families[visit.family_id]

            if self.is_remote(visit):
                return {
                    "visit": self.visit_summary(visit_id),
                    "pairs": [], "pair_problems": [], "candidates": [],
                    "excluded": [], "recommended_id": None,
                    "top_rank_blocked": False,
                    "review_band": round(self.cfg.epsilon_review_band, 3),
                    "close_call": False,
                    "family_preference": None, "named_preference": None,
                    "required_attributes": [],
                    "remote": True,
                    "notices": [{
                        "tone": "info",
                        "code": "REMOTE_CHECKPOINT",
                        "message": (
                            f"No staff needed. A {visit.checkpoint} "
                            f"{visit.protocol} timepoint is questionnaires and, "
                            "only if the child flags, a clinical phone call. "
                            "Nobody travels and no kit is used."),
                    }],
                    "assigned": self.assignments.get(visit_id),
                }

            pool = score_visit(visit, self.state, self.cfg, self.now)
            pool, elig = self._eligible_pool(visit, pool)

            # Layer 1 gates, run over the scored pool. These were written to the
            # Master spec and covered by their own tests, but nothing in the
            # live path ever called them: the board's exclusions came only from
            # the engine's feasibility stage. So the certification rule -- the
            # one the manual states as non-negotiable -- was not being enforced
            # on screen. Anyone a gate rejects is moved out of the ranking
            # entirely rather than ranked and flagged, because a hard rule is
            # not something a good score should be able to argue with.
            survivors, gated = self._apply_gates(visit, family, pool)

            ranked = []
            for cand in survivors:
                vetoes = fairness_violations(
                    cand.coordinator_id, cand, self.state, self.cfg, self.now
                )
                contributions = [
                    {
                        "key": key,
                        "label": CRITERION_LABEL[key],
                        "help": CRITERION_HELP[key],
                        "value": round(getattr(cand.components, key), 3),
                        "weight": round(getattr(self.cfg.weights, key), 3),
                        "contribution": round(cand.contributions.get(key, 0.0), 4),
                    }
                    for key in ("phi", "omega", "psi", "p")
                ]
                lead = max(contributions, key=lambda c: c["contribution"])
                ranked.append(
                    {
                        "id": cand.coordinator_id,
                        "name": cand.coordinator_name,
                        "initials": "".join(
                            p[0] for p in cand.coordinator_name.split()[:2]
                        ).upper(),
                        "rank": cand.rank_position,
                        "score": round(cand.final_score, 3),
                        "gap_to_next": (
                            round(cand.gap_to_next, 3)
                            if cand.gap_to_next is not None
                            else None
                        ),
                        "review_band": cand.review_band_flag,
                        "confidence": confidence_words(cand.selection_stability),
                        "stability": (
                            round(cand.selection_stability, 3)
                            if cand.selection_stability is not None
                            else None
                        ),
                        "contributions": contributions,
                        "leads_on": lead["label"],
                        "slot": (
                            cand.feasibility.slot_start.strftime("%a %-I:%M %p")
                            if cand.feasibility.slot_start
                            else None
                        ),
                        "travel_minutes": round(cand.components.travel_minutes),
                        "prior_visits": cand.components.k_prior_visits,
                        "utilization": round(min(1.5, cand.components.utilization), 3),
                        "did_previous_checkpoint": cand.components.p >= 1.0,
                        "provisional": cand.feasibility.provisional,
                        "soft_flags": list(cand.feasibility.soft_flags),
                        "blocked_by": [VETO_TEXT.get(v, v) for v in vetoes],
                        "assignable": not vetoes,
                        "reason": reason_string(
                            cand, family, self.state.coordinators.get(cand.coordinator_id)
                        ),
                        "evidence": evidence_state(
                            cand.coordinator_id, self.state,
                            cand.feasibility.slot_start or self.now,
                            cand.feasibility.slot_end or self.now, self.now,
                        ),
                        "van_trained": bool(
                            getattr(self.state.coordinators.get(cand.coordinator_id),
                                    "van_trained", False)),
                        "out_of_hours": int(
                            getattr(self.state.coordinators.get(cand.coordinator_id),
                                    "out_of_hours_count", 0)),
                    }
                )

            # The first candidate a human can actually take. The UI gives this
            # person the primary button; anyone past them is an override.
            recommended_id = next(
                (c["id"] for c in ranked if c["assignable"]), None
            )

            excluded = gated + [
                {
                    "id": c.coordinator_id,
                    "name": c.coordinator_name,
                    "reason": describe_fail(c.feasibility.fail_reason),
                }
                for c in pool.rejected
            ]

            pairs, pair_problems = rank_pairs(
                visit, self.state, self.cfg.weights, self.matrix, survivors,
                self.now, duration_hours=visit_duration_hours(visit, family),
                roster=self.roster_config,
            )

            return {
                "visit": self.visit_summary(visit_id),
                "pairs": [p.to_dict() for p in pairs],
                "pair_problems": pair_problems,
                "family_preference": (
                    "Wants a familiar face" if family.sigma >= 0 else "Wants a fresh face"
                ),
                "named_preference": sorted(family.preferred_coordinators),
                "required_attributes": sorted(family.required_attributes),
                "candidates": ranked,
                "recommended_id": recommended_id,
                "top_rank_blocked": bool(
                    ranked and not ranked[0]["assignable"] and recommended_id
                ),
                "excluded": excluded,
                "review_band": round(pool.epsilon_used, 3),
                "close_call": bool(ranked and ranked[0]["review_band"]),
                # Rule by rule, for everybody weighed. A recommendation nobody
                # can interrogate is a recommendation nobody should act on.
                "eligibility": elig.to_dict(),
                "notices": self._notices(pool, ranked),
                "assigned": self.assignments.get(visit_id),
            }

    def _notices(self, pool, ranked) -> List[dict]:
        """Surprise codes, translated out of engineering vocabulary."""
        out: List[dict] = []
        text = {
            "INSIDE_REVIEW_BAND": (
                "warn",
                "Close call. The top two are within the review band, so treat this "
                "as a tie rather than a first and second.",
            ),
            "LOW_SELECTION_STABILITY": (
                "warn",
                "The leader wins less than 60% of the time when the weights are "
                "nudged. Worth a second opinion.",
            ),
            "WEAK_BEST_OPTION": (
                "warn",
                "Even the best available option scores low. Consider widening the "
                "date window.",
            ),
            "TOP_PICK_OVER_CAPACITY": (
                "alert",
                "The top-scoring person is already over their week. They are shown "
                "but cannot be assigned.",
            ),
            "POOL_STARVATION": (
                "alert",
                "One or no coordinators are eligible. The score is not doing much "
                "work here.",
            ),
            "NO_FEASIBLE_CANDIDATE": (
                "alert",
                "Nobody is eligible for this visit. It needs manual scheduling.",
            ),
            "UNVERIFIED_AVAILABILITY": (
                "alert",
                "Availability could not be verified. Confirm in Outlook before "
                "telling the family.",
            ),
        }
        for code in pool.surprise_codes:
            base = code.split(":")[0]
            if base in text:
                tone, message = text[base]
                out.append({"tone": tone, "code": base, "message": message})
        if not ranked:
            out.append(
                {
                    "tone": "alert",
                    "code": "NO_FEASIBLE_CANDIDATE",
                    "message": "Nobody is eligible for this visit.",
                }
            )
        return out

    # -- writes --------------------------------------------------------------

    def assign(
        self,
        visit_id: str,
        coordinator_id: str,
        reason_code: Optional[str] = None,
        reason_text: Optional[str] = None,
        tech_id: Optional[str] = None,
    ) -> dict:
        with self._lock:
            if visit_id in self.assignments:
                raise ValueError("This visit is already assigned.")
            visit = self.visits[visit_id]
            family = self.state.families[visit.family_id]
            pool = score_visit(visit, self.state, self.cfg, self.now)
            pool, _ = self._eligible_pool(visit, pool)
            survivors, gated = self._apply_gates(visit, family, pool)
            if not survivors:
                raise ValueError("Nobody is eligible for this visit.")
            blocked = next((g for g in gated if g["id"] == coordinator_id), None)
            if blocked:
                raise ValueError(blocked["reason"])
            chosen = next(
                (c for c in survivors if c.coordinator_id == coordinator_id), None
            )
            if chosen is None:
                raise ValueError("That coordinator is not eligible for this visit.")
            # An override is choosing past the best option a human could
            # actually take, not past rank 1. When a fairness veto blocks the
            # top-ranked person, the next assignable candidate IS the board's
            # recommendation; the engine already records that skip as a system
            # constraint rather than a human override, and demanding a reason
            # for it would both mislabel the decision and pollute the override
            # log that weight re-elicitation depends on.
            assignable = [
                c for c in survivors
                if not fairness_violations(c.coordinator_id, c, self.state, self.cfg, self.now)
            ]
            recommended = assignable[0].coordinator_id if assignable else None
            is_override = recommended is not None and coordinator_id != recommended
            if is_override and not reason_code:
                raise ValueError(
                    "Choosing past the top suggestion needs a reason. "
                    "An unexplained override is a lost data point."
                )
            if reason_code and reason_code not in OVERRIDE_REASON_CODES:
                raise ValueError(f"Unknown reason code {reason_code!r}.")

            run_id, committed, notes = commit_assignment(
                pool,
                self.state,
                self.cfg,
                self.store,
                provider=self.provider,
                chosen_coordinator_id=coordinator_id,
                override_reason_code=reason_code if is_override else None,
                override_reason_text=reason_text if is_override else None,
                overridden_by="visitboard" if is_override else None,
                now=self.now,
                tech_id=tech_id,
            )
            if committed is None:
                raise ValueError("; ".join(notes) or "The assignment was refused.")

            tech = self.state.coordinators.get(tech_id) if tech_id else None
            record = {
                "run_id": run_id,
                "tech_id": tech_id,
                "tech_name": getattr(tech, "name", None),
                "coordinator_id": committed.coordinator_id,
                "coordinator_name": committed.coordinator_name,
                "rank": committed.rank_position,
                "score": round(committed.final_score, 3),
                "override": is_override,
                "reason_code": reason_code if is_override else None,
                "reason_class": (
                    OVERRIDE_REASON_CODES.get(reason_code) if is_override else None
                ),
                "provisional": committed.feasibility.provisional,
                "slot": (
                    committed.feasibility.slot_start.strftime("%a %-I:%M %p")
                    if committed.feasibility.slot_start
                    else None
                ),
                "notes": notes,
            }
            self.assignments[visit_id] = record
            self._attention_cache.clear()
            label = self.visit_summary(visit_id)["family_label"]
            if is_override:
                self._log(
                    f"{label}: chose {committed.coordinator_name} over the top "
                    f"suggestion ({reason_code})."
                )
            else:
                self._log(f"{label}: assigned {committed.coordinator_name}.")
            return record

    def unassign(self, visit_id: str) -> None:
        with self._lock:
            record = self.assignments.pop(visit_id, None)
            self._attention_cache.clear()
            if not record:
                return
            pending = self.state.pending.get(record["coordinator_id"], [])
            self.state.pending[record["coordinator_id"]] = [
                v for v in pending if v.visit_id != visit_id
            ]
            self._log(
                f"{self.visit_summary(visit_id)['family_label']}: assignment undone."
            )

    # -- analytics -----------------------------------------------------------

    def fairness(self) -> dict:
        with self._lock:
            start = self.now
            end = self.now + timedelta(days=7)
            rep = weekly_drift(self.store, self.cfg, start, end)
            names = {c.coordinator_id: c.name for c in self.state.active_coordinators()}
            rows = []
            for c in sorted(
                self.state.active_coordinators(), key=lambda c: c.coordinator_id
            ):
                assigned = [
                    a for a in self.assignments.values()
                    if a["coordinator_id"] == c.coordinator_id
                ]
                committed = self.state.committed(c.coordinator_id)
                rows.append(
                    {
                        "id": c.coordinator_id,
                        "name": c.name,
                        "visits": len(assigned),
                        "hours": round(committed, 1),
                        "capacity": round(c.capacity_hours_week, 1),
                        "utilization": round(
                            min(1.5, committed / max(1e-6, c.capacity_hours_week)), 3
                        ),
                    }
                )
            loads = [r["utilization"] for r in rows]
            mean = sum(loads) / len(loads) if loads else 0.0
            spread = (max(loads) / mean) if mean > 0 else 0.0
            return {
                "rows": rows,
                "cv": round(rep.cv_utilization, 3),
                "imbalance": round(spread, 2),
                "permutation_p": round(rep.permutation_p, 3),
                "assigned": len(self.assignments),
                "total": len(self.order),
                "override_rate": round(rep.override_rate, 3),
                "status": (
                    "even" if spread < 1.4 else "uneven" if spread < 1.9 else "lopsided"
                ),
            }

    def week_plan(self) -> dict:
        with self._lock:
            visits = [self.visits[v] for v in self.order if v not in self.assignments]
            if not visits:
                return {"regret": 0.0, "greedy": 0.0, "optimal": 0.0, "escalate": False,
                        "unfilled_gap": 0, "note": "Everything is assigned."}
            greedy, optimal, report, _ = plan_week(
                visits, self.state, self.cfg, self.now
            )
            return {
                "regret": round(report.regret, 4),
                "greedy": round(report.greedy_total, 3),
                "optimal": round(report.optimal_total, 3),
                "unfilled_gap": report.unfilled_gap,
                "escalate": report.escalate,
                "note": report.note,
            }

    def reason_codes(self) -> List[dict]:
        pretty = {
            "family_request": "The family asked for someone specific",
            "coordinator_request": "The coordinator asked to take or skip it",
            "clinical_judgment": "Clinical judgement",
            "training_opportunity": "Training or pairing opportunity",
            "calendar_data_wrong": "The calendar was wrong",
            "credential_data_wrong": "The credential record was wrong",
            "travel_data_wrong": "The travel estimate was wrong",
            "history_data_wrong": "The visit history was wrong",
        }
        return [
            {
                "code": code,
                "label": pretty[code],
                "cls": OVERRIDE_REASON_CODES[code],
            }
            for code in pretty
        ]
