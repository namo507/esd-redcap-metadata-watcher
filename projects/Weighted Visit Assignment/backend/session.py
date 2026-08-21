"""In-memory lab session: the state the board reads and writes.

One process holds one lab. The engine is deterministic and the audit store is
append-only SQLite, so a restart rebuilds the same synthetic lab and keeps every
decision already recorded.

Nothing here invents scheduling logic. Every judgement comes from
``esd_scheduler``; this module only decides what the screen needs to see.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
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
from esd_scheduler.drift import weekly_drift
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
from esd_scheduler.schedule import STATUS_LABEL, ProtocolSchedule, upcoming
from esd_scheduler.scoring import ramped_capacity
from esd_scheduler.engine import (
    commit_assignment,
    fairness_violations,
    plan_week,
    score_visit,
)
from esd_scheduler.models import BusyBlock
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


class LabSession:
    """Thread-safe wrapper around one LabState plus its audit store."""

    def __init__(self, db_path: str = os.path.join("data", "visitboard.db")) -> None:
        self._lock = threading.RLock()
        self.db_path = db_path
        self.cfg: EngineConfig = load_config()
        self.matrix = ReliabilityMatrix.load()
        self.reset()

    # -- lifecycle -----------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            # Two different clocks, and conflating them is what made the board
            # claim "synced 40 minutes ago" hours after it had actually synced.
            #
            #   epoch : start of this week. The synthetic lab is built against
            #           it so the demo's visits always land in the current week.
            #   now   : the real wall clock, read fresh every time. Evidence
            #           ages, protocol windows and the header all read from it.
            self.epoch = datetime.now().replace(
                hour=9, minute=0, second=0, microsecond=0)
            self.epoch -= timedelta(days=self.epoch.weekday())   # anchor on Monday
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
            self._log("Board reset. Synthetic lab rebuilt from the roster.")

    # -- calendar uploads ----------------------------------------------------

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

    def _apply_confirmed_blocks(self) -> int:
        """Push reviewed-and-confirmed blocks into the live snapshots.

        Only confirmed blocks land. An unreviewed parse is not evidence, so it
        must not narrow anybody's availability while it waits for a human.
        """
        rows = [dict(r) for r in self.store.confirmed_blocks()]
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
                continue
            keep = [b for b in snap.blocks if getattr(b, "source", None) != "pdf_import"]
            snap.blocks = keep + blocks
            # Stamp with the board's own clock, not the wall clock. The demo lab
            # is anchored to the start of the week, so a wall-clock stamp reads
            # as evidence from the future and the freshness gate goes negative.
            snap.fetched_at = self.now
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
                "coordinator": getattr(
                    self.state.coordinators.get(b["coordinator_id"]), "name",
                    b["coordinator_id"]),
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
            "weight_vector_id": self.cfg.weights_id
            if hasattr(self.cfg, "weights_id") else None,
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

    def health(self) -> dict:
        return {
            "ok": True,
            "engine_version": ENGINE_VERSION,
            "weight_vector_id": self.cfg.weight_vector_id,
            "config_fingerprint": self.cfg.fingerprint(),
            "review_band": round(self.cfg.epsilon_review_band, 3),
            "review_band_calibrated": self.cfg.epsilon_calibrated,
            "graph_auth_mode": self.cfg.graph_auth_mode,
            "calendar_source": "demo",
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
            "family_label": f"Family {v.family_id[1:]}",
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

    def candidates(self, visit_id: str) -> dict:
        """The full ranked pool for one visit, in the words the screen needs."""
        with self._lock:
            visit = self.visits[visit_id]
            pool = score_visit(visit, self.state, self.cfg, self.now)
            family = self.state.families[visit.family_id]

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

            return {
                "visit": self.visit_summary(visit_id),
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
    ) -> dict:
        with self._lock:
            if visit_id in self.assignments:
                raise ValueError("This visit is already assigned.")
            visit = self.visits[visit_id]
            family = self.state.families[visit.family_id]
            pool = score_visit(visit, self.state, self.cfg, self.now)
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
            )
            if committed is None:
                raise ValueError("; ".join(notes) or "The assignment was refused.")

            record = {
                "run_id": run_id,
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
