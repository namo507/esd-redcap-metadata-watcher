"""Who may staff a visit at all, decided before anybody is scored.

This module knows nothing about reading calendars and nothing about the
weighted score. It answers one question -- *is this person allowed on this
visit* -- and it answers it the same way every time, in writing.

    calendar read  ->  ELIGIBILITY  ->  weighted score  ->  ranking
                       (this file)

WHY IT IS SEPARATE. Availability, competence and preference are three
different kinds of fact and they fail differently. A busy afternoon is a
scheduling problem. Not being signed off on the Bayley is not: no amount of
being free, being owed a visit, or having seen the family before makes
somebody able to run an assessment they have not been trained on. Keeping the
hard rules in their own module means a change to the scoring weights can never
loosen them by accident, and a change here can never quietly reweight anything.

WHAT IT REFUSES TO DO. It will not let two half-qualified people add up to one
qualified clinician. The manual asks for one person who can "reliably and
independently" administer every assessment the visit needs, so coverage is
checked against the designated clinician alone, never against the pair.

Everything it consults is data in ``config/visit-profiles.json``: the
assessment catalog, the visit profiles, the requirements, the pairing
constraints and the special rules. No name and no assessment appears in a
conditional in this file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

CONFIG_PATH = os.path.join("config", "visit-profiles.json")

# The rules, in the order they are asked. Order matters twice over: it is the
# order a scheduler would ask them out loud, and the first failure is the one
# reported, so a person missing an assessment is told that rather than being
# told about a pairing rule they never reached.
RULE_ORDER = (
    "role",              # can they hold this seat at all
    "solo_range",        # can they run a visit of this age alone
    "assessments",       # signed off on every assessment this visit needs
    "special_rule",      # protocol or population rules
    "pairing",           # prohibited pairs, supervision, partner requirements
    "availability",      # a calendar has actually been read for them
)

RULE_LABEL = {
    "role": "Role",
    "solo_range": "Can run this visit",
    "assessments": "Assessment sign-off",
    "special_rule": "Special rule",
    "pairing": "Pairing",
    "availability": "Availability",
}


# --------------------------------------------------------------------- config

@dataclass
class Assessment:
    code: str
    name: str = ""
    protocol: str = ""
    time_point: str = ""
    version: str = "standard"
    requires_clinician_reliability: bool = True
    can_tech_assist: bool = False
    notes: str = ""


@dataclass
class VisitProfile:
    id: str
    protocol: str = ""
    time_point: str = ""
    setting: str = "home"
    duration_minutes: int = 120
    clinicians: int = 1
    techs: int = 1
    friday_allowed: bool = False
    grad_student_allowed: bool = True
    requires_vehicle: bool = False
    notes: str = ""

    @property
    def is_remote(self) -> bool:
        return self.setting == "remote" or (self.clinicians + self.techs) == 0


@dataclass
class Requirement:
    profile: str
    assessment: str
    type: str = "required"          # required | conditional | alternative
    alternative_group: str = ""
    condition: str = ""


@dataclass
class PairingConstraint:
    id: str
    person_a: str
    person_b: str
    type: str                       # prohibited | requires_partner | warning
    applies_to_profile: str = "all"
    active: bool = False
    reason: str = ""
    approval_owner: str = ""


@dataclass
class SpecialRule:
    id: str
    name: str
    priority: str = "preference"    # hard_block | mandatory | preference | warning
    protocol: str = "all"
    time_point: str = "all"
    population_flag: str = ""
    required_role: str = "clinician"
    required_coordinator: str = ""
    active: bool = False
    expression: str = ""
    evidence: str = ""
    approval_owner: str = ""


@dataclass
class ProfileConfig:
    catalog: Dict[str, Assessment] = field(default_factory=dict)
    profiles: Dict[str, VisitProfile] = field(default_factory=dict)
    requirements: List[Requirement] = field(default_factory=list)
    pairing: List[PairingConstraint] = field(default_factory=list)
    special: List[SpecialRule] = field(default_factory=list)
    confirmed: bool = False

    # -- lookups ------------------------------------------------------------

    def profile_for(self, protocol: str, time_point: str) -> Optional[VisitProfile]:
        """The profile matching a protocol and time point, if one is declared."""
        protocol = (protocol or "").upper()
        for prof in self.profiles.values():
            if prof.protocol.upper() == protocol and prof.time_point == time_point:
                return prof
        return None

    def required_assessments(self, profile_id: str):
        """Returns (must_hold, alternative_groups).

        ``must_hold`` is every assessment needed on its own. Each alternative
        group is a list where holding any one satisfies the group, which is how
        "ADOS plus Mullen or DAS" is expressed without a special case.
        Conditional requirements are reported separately and never block: the
        manual makes them a clinical judgement, not a scheduling rule.
        """
        must: List[str] = []
        groups: Dict[str, List[str]] = {}
        for req in self.requirements:
            if req.profile != profile_id:
                continue
            if req.type == "required":
                must.append(req.assessment)
            elif req.type == "alternative" and req.alternative_group:
                groups.setdefault(req.alternative_group, []).append(req.assessment)
        return must, list(groups.values())

    def conditional_assessments(self, profile_id: str) -> List[Requirement]:
        return [r for r in self.requirements
                if r.profile == profile_id and r.type == "conditional"]

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ProfileConfig":
        path = path or os.environ.get("ESD_PROFILES_PATH", CONFIG_PATH)
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)

        def pick(row, keys):
            return {k: row[k] for k in keys if k in row}

        catalog = {}
        for row in raw.get("assessment_catalog", []):
            if row.get("code"):
                catalog[row["code"]] = Assessment(**pick(row, (
                    "code", "name", "protocol", "time_point", "version",
                    "requires_clinician_reliability", "can_tech_assist", "notes")))

        profiles = {}
        for row in raw.get("visit_profiles", []):
            if row.get("id"):
                profiles[row["id"]] = VisitProfile(**pick(row, (
                    "id", "protocol", "time_point", "setting",
                    "duration_minutes", "clinicians", "techs", "friday_allowed",
                    "grad_student_allowed", "requires_vehicle", "notes")))

        return cls(
            catalog=catalog,
            profiles=profiles,
            requirements=[
                Requirement(**pick(r, ("profile", "assessment", "type",
                                       "alternative_group", "condition")))
                for r in raw.get("visit_assessment_requirements", [])
                if r.get("profile") and r.get("assessment")
            ],
            pairing=[
                PairingConstraint(**pick(r, (
                    "id", "person_a", "person_b", "type", "applies_to_profile",
                    "active", "reason", "approval_owner")))
                for r in raw.get("pairing_constraints", []) if r.get("id")
            ],
            special=[
                SpecialRule(**pick(r, (
                    "id", "name", "priority", "protocol", "time_point",
                    "population_flag", "required_role", "required_coordinator",
                    "active", "expression", "evidence", "approval_owner")))
                for r in raw.get("special_assignment_rules", []) if r.get("id")
            ],
            confirmed=bool(raw.get("confirmed")),
        )


# --------------------------------------------------------------------- result

@dataclass
class Verdict:
    """One person weighed against one visit, rule by rule."""
    coordinator_id: str
    name: str
    seat: str                        # clinician | tech
    checks: Dict[str, bool] = field(default_factory=dict)
    eligible: bool = True
    failed_rule: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "coordinator_id": self.coordinator_id,
            "name": self.name,
            "seat": self.seat,
            "checks": dict(self.checks),
            "eligible": self.eligible,
            "failed_rule": self.failed_rule,
            "failed_rule_label": RULE_LABEL.get(self.failed_rule, ""),
            "reason": self.reason,
        }


@dataclass
class EligibilityResult:
    visit_id: str
    profile_id: str
    clinicians: List[Verdict] = field(default_factory=list)
    techs: List[Verdict] = field(default_factory=list)
    excluded: List[Verdict] = field(default_factory=list)
    # Made the tech seat but not the clinician one. Not the same as excluded,
    # and worth saying separately: "can tech, cannot run it" is the answer to
    # a question a scheduler actually asks, and burying it in the eligible
    # list loses the reason entirely.
    clinician_blocked: List[Verdict] = field(default_factory=list)
    needs_review: List[Verdict] = field(default_factory=list)
    remote: bool = False
    notes: List[str] = field(default_factory=list)
    evaluated_at: str = ""

    @property
    def eligible_ids(self) -> List[str]:
        """Everyone who can hold either seat. This is what gets scored."""
        return sorted({v.coordinator_id for v in self.clinicians + self.techs})

    @property
    def has_anyone(self) -> bool:
        return bool(self.clinicians and self.techs)

    def to_dict(self) -> dict:
        return {
            "visit_id": self.visit_id,
            "profile_id": self.profile_id,
            "remote": self.remote,
            "clinicians": [v.to_dict() for v in self.clinicians],
            "techs": [v.to_dict() for v in self.techs],
            "excluded": [v.to_dict() for v in self.excluded],
            "clinician_blocked": [v.to_dict() for v in self.clinician_blocked],
            "needs_review": [v.to_dict() for v in self.needs_review],
            "eligible_ids": self.eligible_ids,
            "has_anyone": self.has_anyone,
            "notes": list(self.notes),
            "evaluated_at": self.evaluated_at,
        }


# ---------------------------------------------------------------- the engine

def evaluate(
    visit,
    candidates: Sequence[str],
    roster,
    matrix,
    now: datetime,
    config: Optional[ProfileConfig] = None,
    availability=None,
) -> EligibilityResult:
    """Decide who may staff this visit, and record why for everyone else.

    ``candidates`` are coordinator ids. ``availability`` is optional and, when
    given, must answer ``confirmed(coordinator_id) -> bool``: whether a
    calendar has actually been read and confirmed for them. Anyone it cannot
    confirm is put in ``needs_review`` rather than into either seat, because an
    unread calendar is unknown and unknown is not free.
    """
    config = config or ProfileConfig.load()
    profile = config.profile_for(visit.protocol, visit.checkpoint)
    result = EligibilityResult(
        visit_id=getattr(visit, "visit_id", ""),
        profile_id=profile.id if profile else "",
        evaluated_at=now.isoformat(timespec="seconds"),
    )

    if profile is None:
        result.notes.append(
            f"No visit profile is declared for {visit.protocol} {visit.checkpoint}, "
            f"so the assessment requirements could not be checked. Add one to "
            f"config/visit-profiles.json.")
    elif profile.is_remote:
        result.remote = True
        result.notes.append(
            f"{profile.id} is a remote timepoint: nobody attends, so no one "
            f"needs to be staffed.")
        return result

    must_hold, groups = ([], [])
    if profile:
        must_hold, groups = config.required_assessments(profile.id)

    by_id = roster.by_id()
    for cid in candidates:
        entry = by_id.get(cid)
        if entry is None:
            continue
        name = getattr(entry, "name", cid)

        for seat in ("clinician", "tech"):
            verdict = _weigh(entry, seat, visit, profile, must_hold, groups,
                             roster, matrix, config, availability)
            verdict.coordinator_id, verdict.name = cid, name
            if verdict.eligible:
                (result.clinicians if seat == "clinician"
                 else result.techs).append(verdict)
            elif verdict.failed_rule == "availability":
                if not any(v.coordinator_id == cid for v in result.needs_review):
                    result.needs_review.append(verdict)
            elif seat == "clinician":
                # One row per person in the excluded list, keyed on the
                # clinician verdict: a scheduler wants "why not them", not two
                # rows saying nearly the same thing.
                result.excluded.append(verdict)

    # Somebody who can only tech is not excluded from the visit, but the
    # reason they cannot run it is still worth having. Move them rather than
    # dropping them, so nothing is silently lost.
    teching = {v.coordinator_id for v in result.techs}
    result.clinician_blocked = [v for v in result.excluded
                                if v.coordinator_id in teching]
    result.excluded = [v for v in result.excluded
                       if v.coordinator_id not in teching]
    return result


def _weigh(entry, seat, visit, profile, must_hold, groups,
           roster, matrix, config, availability) -> Verdict:
    """One person, one seat, every rule in order. First failure wins."""
    verdict = Verdict(coordinator_id="", name="", seat=seat)

    # 1. role
    roles = [r.lower() for r in (getattr(entry, "roles", []) or [])]
    ok = seat in roles
    verdict.checks["role"] = ok
    if not ok:
        return _fail(verdict, "role",
                     f"Not recorded as a {seat} on the roster.")

    # 2. can they run a visit of this age alone (clinicians only)
    if seat == "clinician":
        ok = roster.can_be_clinician_for(entry, visit.checkpoint)
        verdict.checks["solo_range"] = ok
        if not ok:
            return _fail(verdict, "solo_range",
                         f"The reliability chart prints no solo range covering "
                         f"{visit.checkpoint}, so they cannot run this visit "
                         f"on their own.")
    else:
        verdict.checks["solo_range"] = True

    # 3. assessments. Only the clinician has to hold them: the manual asks for
    #    one person who can administer everything independently.
    if seat == "clinician" and profile is not None:
        # An assessment the sign-off chart does not track cannot gate anyone.
        # PIX/RIX and OIX are in the catalog because the visit includes them,
        # but nobody is ever "signed off" on them, and treating an untracked
        # code as unmet would exclude the entire roster from every visit.
        def held(code: str) -> Optional[bool]:
            key = _matrix_code(code, matrix)
            if not key:
                return None                      # not a gating assessment
            return matrix.is_reliable(entry.id, key)

        missing = [a for a in must_hold if held(a) is False]
        unmet = []
        for group in groups:
            answers = [held(a) for a in group]
            if any(x is None for x in answers):
                continue                         # the chart cannot judge this
            if not any(answers):
                unmet.append(group)
        ok = not missing and not unmet
        verdict.checks["assessments"] = ok
        if not ok:
            names = [_pretty(config, a) for a in missing]
            for group in unmet:
                names.append(" or ".join(_pretty(config, a) for a in group))
            return _fail(verdict, "assessments",
                         "Not signed off on " + ", ".join(names) + ".")
    else:
        verdict.checks["assessments"] = True

    # 4. special rules
    failed = _special_failure(entry, seat, visit, config)
    verdict.checks["special_rule"] = failed is None
    if failed:
        return _fail(verdict, "special_rule", failed)

    # 5. pairing rules that apply to the person rather than to a pair. A
    #    trainee or graduate student needing a partner is not excluded here;
    #    the pair step enforces it, and saying "ineligible" would be wrong.
    verdict.checks["pairing"] = True

    # 6. availability, last because it is the only one that changes hourly
    if availability is not None:
        ok = bool(availability.confirmed(entry.id))
        verdict.checks["availability"] = ok
        if not ok:
            return _fail(verdict, "availability",
                         "No confirmed calendar for this week, so their time "
                         "is unknown rather than free. Confirm the read to "
                         "bring them back in.")
    else:
        verdict.checks["availability"] = True

    return verdict


def _fail(verdict: Verdict, rule: str, reason: str) -> Verdict:
    verdict.eligible = False
    verdict.failed_rule = rule
    verdict.reason = reason
    for later in RULE_ORDER[RULE_ORDER.index(rule) + 1:]:
        verdict.checks.setdefault(later, None)     # not reached, not failed
    return verdict


def _special_failure(entry, seat, visit, config) -> Optional[str]:
    """The first active special rule this person falls foul of."""
    for rule in config.special:
        if not rule.active or rule.priority not in ("hard_block", "mandatory"):
            continue
        if rule.protocol not in ("all", visit.protocol):
            continue
        if rule.time_point != "all" and visit.checkpoint not in [
                t.strip() for t in rule.time_point.split(",")]:
            continue
        if rule.required_role not in ("either", seat):
            continue
        if rule.required_coordinator and entry.id != rule.required_coordinator:
            return f"{rule.name}: this visit requires a named person."
    return None


def _matrix_code(catalog_code: str, matrix) -> str:
    """Map a catalog code onto the reliability matrix's own key.

    The catalog and the matrix grew up separately: the catalog is the manual's
    assessment list with versions, the matrix is the sign-off chart. Where the
    two use different spellings for the same thing this is the one place that
    knows it, rather than the knowledge being spread through the gate.
    """
    aliases = {
        "ORIENTATION_1_3M": "Orientation_1_3m",
        "BAYLEY_MOTOR_3M": "Bayley_3m",
        "CSBS_MODIFIED_6M": "CSBS_6m",
        "CSBS_9_12M": "CSBS_9_12m",
        "BAYLEY_9_12M": "Bayley_9_12m",
        "ADOS_36M": "ADOS",
        "DAS_36M": "DAS_II",
        "DAS_48M": "DAS_II",
        "MULLEN_36M": "Mullen",
        "MULLEN_48M_OPTIONAL": "Mullen",
    }
    mapped = aliases.get(catalog_code, catalog_code)
    # An assessment the chart does not track cannot gate anybody: saying "not
    # signed off" about something nobody is ever signed off on would exclude
    # the whole roster. Those are the tech-assistable ones -- PIX/RIX, OIX --
    # which the catalog marks as needing no clinician reliability.
    return mapped if mapped in (matrix.assessments or []) else ""


def _pretty(config: ProfileConfig, code: str) -> str:
    entry = config.catalog.get(code)
    return entry.name if entry and entry.name else code
