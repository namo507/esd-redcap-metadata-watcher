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
from esd_scheduler.calendarsync import MockProvider
from esd_scheduler.config import EngineConfig, load_config
from esd_scheduler.demo import build_lab
from esd_scheduler.drift import weekly_drift
from esd_scheduler.engine import (
    commit_assignment,
    fairness_violations,
    plan_week,
    score_visit,
)
from esd_scheduler.store import OVERRIDE_REASON_CODES, AuditStore

CRITERION_LABEL = {
    "phi": "Continuity",
    "omega": "Family preference",
    "psi": "Burden relief",
    "p": "Protocol continuity",
}
CRITERION_HELP = {
    "phi": "How well this coordinator already knows the family, faded by how long ago.",
    "omega": "What the family asked for. Neutral when nothing is on record.",
    "psi": "How much room is left in their week, counting travel as work.",
    "p": "Did this person run the family's previous checkpoint.",
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


def confidence_words(stability: Optional[float]) -> str:
    """Never show a coordinator a probability. Show them a judgement."""
    if stability is None:
        return "Only option"
    if stability >= 0.85:
        return "Clear choice"
    if stability >= 0.60:
        return "Slight edge"
    return "Too close to call"


class LabSession:
    """Thread-safe wrapper around one LabState plus its audit store."""

    def __init__(self, db_path: str = os.path.join("data", "visitboard.db")) -> None:
        self._lock = threading.RLock()
        self.db_path = db_path
        self.cfg: EngineConfig = load_config()
        self.reset()

    # -- lifecycle -----------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self.now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            self.now -= timedelta(days=self.now.weekday())  # anchor on Monday
            self.state, visits = build_lab(self.now)
            self.visits: Dict[str, object] = {v.visit_id: v for v in visits}
            self.order: List[str] = [v.visit_id for v in visits]
            self.provider = MockProvider(
                blocks=getattr(self.state, "demo_blocks", {}), clock=lambda: self.now
            )
            self.assignments: Dict[str, dict] = {}
            self.activity: List[dict] = []
            if getattr(self, "store", None):
                try:
                    self.store.close()
                except Exception:  # noqa: BLE001
                    pass
            self.store = AuditStore(self.db_path)
            self.store.record_config(self.cfg)
            self._log("Board reset. Synthetic lab rebuilt from the roster.")

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
            "week_of": self.now.strftime("%Y-%m-%d"),
        }

    def roster(self) -> List[dict]:
        with self._lock:
            out = []
            for c in sorted(
                self.state.active_coordinators(), key=lambda c: c.coordinator_id
            ):
                committed = self.state.committed(c.coordinator_id)
                capacity = max(1e-6, c.capacity_hours_week)
                out.append(
                    {
                        "id": c.coordinator_id,
                        "name": c.name,
                        "initials": "".join(p[0] for p in c.name.split()[:2]).upper(),
                        "credentials": sorted(c.credentials),
                        "capacity_hours": round(c.capacity_hours_week, 1),
                        "committed_hours": round(committed, 1),
                        "utilization": round(min(1.5, committed / capacity), 3),
                        "visits_this_week": self.state.visits_this_week(
                            c.coordinator_id, self.now
                        ),
                        "is_new": c.n_completed_visits < self.cfg.n_min_visits,
                    }
                )
            return out

    def visit_summary(self, visit_id: str) -> dict:
        v = self.visits[visit_id]
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
            "duration_hours": v.duration_hours,
            "status": "assigned" if assigned else "needs_assignment",
            "assigned_to": assigned["coordinator_name"] if assigned else None,
            "assigned_id": assigned["coordinator_id"] if assigned else None,
            "provisional": bool(assigned and assigned.get("provisional")),
            "was_override": bool(assigned and assigned.get("override")),
        }

    def queue(self) -> List[dict]:
        with self._lock:
            return [self.visit_summary(vid) for vid in self.order]

    def candidates(self, visit_id: str) -> dict:
        """The full ranked pool for one visit, in the words the screen needs."""
        with self._lock:
            visit = self.visits[visit_id]
            pool = score_visit(visit, self.state, self.cfg, self.now)
            family = self.state.families[visit.family_id]

            ranked = []
            for cand in pool.candidates:
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
                    }
                )

            excluded = [
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
            pool = score_visit(visit, self.state, self.cfg, self.now)
            if not pool.candidates:
                raise ValueError("Nobody is eligible for this visit.")
            chosen = next(
                (c for c in pool.candidates if c.coordinator_id == coordinator_id), None
            )
            if chosen is None:
                raise ValueError("That coordinator is not eligible for this visit.")
            is_override = chosen.rank_position != 1
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
