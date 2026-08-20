"""Deterministic synthetic lab, for the pilot dry run and the tests.

The coordinator NAMES are the real roster from the shared Outlook calendar
view; every attribute attached to them is synthetic. See the note on ``specs``.

Shaped to be awkward on purpose: two overloaded coordinators, one example
coordinator with an empty history, one family with a hard exclusion, one family that wants a
fresh face, one family that names a coordinator, one Spanish-language
requirement, credential gaps that bite differently on NICO and NANO, and travel
correlated with zone so the burden term is signal rather than noise.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from .constraints import apply_visit_duration
from .models import (
    BusyBlock,
    CalendarSnapshot,
    CompletedVisit,
    Coordinator,
    Family,
    LabState,
    Protocol,
    Visit,
)

SEED = 42
CREDENTIALS = ["ADOS", "CONSENT", "PHLEBOTOMY", "DRIVING", "EEG"]


def build_lab(now: datetime, seed: int = SEED) -> Tuple[LabState, List[Visit]]:
    rng = random.Random(seed)
    state = LabState()

    # --- coordinators -------------------------------------------------------
    # The NAMES are the real lab roster, taken from the shared Outlook view
    # (Calendar - Shrivastava, Namit), so the demo, the dashboard and the audit
    # log all refer to the same six people.
    #
    # EVERY ATTRIBUTE BELOW IS SYNTHETIC. Credentials, capacity, completed-visit
    # counts, zones and language attributes are invented to exercise the engine
    # and describe nobody's actual qualifications or workload. Replace them from
    # the real roster before the pilot.
    #
    # The seventh row is a deliberate placeholder rather than a real person: the
    # cold-start path needs someone with no history, and labelling a named
    # colleague "brand new hire" would be a fabricated claim about them.
    specs = [
        # name,                     credentials,                        cap,  done, zone, attrs
        ("Margaret Bell",           {"ADOS", "CONSENT", "DRIVING", "EEG"},        20.0, 61, 1, {"spanish"}),
        ("Lauren Puttock",          {"CONSENT", "DRIVING", "EEG"},                20.0, 48, 2, set()),
        ("Sanjana Oak",             {"ADOS", "CONSENT", "DRIVING"},               20.0, 55, 1, set()),
        ("Sofia Tous",              {"ADOS", "CONSENT", "DRIVING", "PHLEBOTOMY"}, 20.0, 72, 3, set()),
        ("Morgan Soto",             {"CONSENT", "DRIVING"},                       10.0, 33, 2, {"spanish"}),
        ("Ramiro Lucas-Mariano",    {"ADOS", "CONSENT", "DRIVING", "EEG"},        20.0, 40, 4, set()),
        ("New Coordinator (example)", {"CONSENT", "DRIVING", "EEG"},              20.0,  0, 2, set()),
    ]
    for i, (name, creds, cap, done, zone, attrs) in enumerate(specs):
        cid = f"C{i + 1:02d}"
        state.coordinators[cid] = Coordinator(
            coordinator_id=cid,
            name=name,
            credentials=set(creds),
            capacity_hours_week=cap,
            n_completed_visits=done,
            attributes=set(attrs),
            hire_date=(now - timedelta(days=900 if done else 9)).date(),
            working_blocks=[(d, 8.0, 17.0) for d in range(5)],
            # Master §7 staff fields. Synthetic, like everything else here.
            tech_trained=i in (0, 1, 5),
            van_trained=i in (0, 2, 3),
            in_lab_day=(i % 4) if done else None,
            out_of_hours_count=[3, 0, 5, 1, 2, 0, 0][i],
        )
    ids = sorted(state.coordinators)

    # Two people are already heavily booked this period, so the burden term bites.
    state.committed_hours = {cid: round(rng.uniform(3.0, 9.0), 1) for cid in ids}
    # Busy, not capped. Starting anyone above the utilisation ceiling turns the
    # whole run into a demonstration of the fairness veto and hides the ranking,
    # which is the part that needs looking at.
    state.committed_hours["C01"] = 13.0   # busiest
    state.committed_hours["C04"] = 12.0   # second busiest
    state.committed_hours["C07"] = 0.0    # the example new coordinator

    # --- families -----------------------------------------------------------
    for i in range(12):
        fid = f"F{5030 + i}"
        protocol = "NICO" if i % 2 == 0 else "NANO"
        state.families[fid] = Family(
            family_id=fid,
            protocol=protocol,
            zone=rng.randint(1, 4),
            sigma=1,
        )
    # The twins case: a family comfort issue became a hard exclusion.
    state.families["F5034"].hard_exclusions = {"C04"}   # Sofia Tous excluded
    state.families["F5035"].hard_exclusions = {"C04"}
    # One family wants a fresh face after a difficult visit.
    state.families["F5037"].sigma = -1
    # One family asked for someone by name, one has a soft avoid.
    state.families["F5031"].preferred_coordinators = {"C01"}
    state.families["F5033"].soft_avoid = {"C02"}
    # One family needs a Spanish-speaking coordinator.
    state.families["F5039"].required_attributes = {"spanish"}

    # --- Master prompt §6/§7 participant detail ------------------------------
    # Contact method, drive time and free-text notes are what a scheduler reads
    # right before they pick up the phone, so the demo carries realistic ones.
    contact_cycle = ["Text", "Email", "Call"]
    for i, (fid, fam) in enumerate(sorted(state.families.items())):
        fam.preferred_contact_method = contact_cycle[i % 3]
        fam.drive_time_minutes = round(12 + 9 * fam.zone + rng.uniform(-4, 8), 1)
    state.families["F5032"].scheduling_notes = (
        "Fragrance sensitivity: no scented products, please.")
    state.families["F5036"].scheduling_notes = (
        "Weekends only until mid-September; father works nights.")
    state.families["F5030"].scheduling_notes = (
        "Street parking is tight after 4pm. Park on the cross street.")
    # One NDD cross-collaboration visit, to exercise the override and the
    # 60-minute duration extension.
    state.families["F5038"].is_ndd_cross_collab = True
    state.families["F5041"].van_inaccessible = True
    state.families["F5035"].childcare_needed = True

    # --- travel: correlated with zone distance ------------------------------
    for cid in ids:
        czone = state.coordinators[cid].coordinator_id
        czone_num = {"C01": 1, "C02": 2, "C03": 1, "C04": 3,
                     "C05": 2, "C06": 4, "C07": 2}[czone]
        for fid, fam in state.families.items():
            base = 18 + 22 * abs(czone_num - fam.zone)
            state.travel_minutes[(cid, fid)] = round(base + rng.uniform(-6, 10), 1)
            state.travel_source[(cid, fid)] = "cached"

    # --- history ------------------------------------------------------------
    checkpoints = {"NICO": Protocol.default_table()["NICO"].checkpoint_sequence,
                   "NANO": Protocol.default_table()["NANO"].checkpoint_sequence}
    vid = 0
    for fid, fam in state.families.items():
        n_past = rng.randint(0, 3)
        eligible = [
            c for c in ids
            if state.coordinators[c].holds(
                Protocol.default_table()[fam.protocol].required_credentials
            )
            and c not in fam.hard_exclusions
            and state.coordinators[c].n_completed_visits > 0
        ]
        if not eligible:
            continue
        # Real labs keep a primary coordinator with a family, so most past
        # visits go to the same person. Without this the continuity term looks
        # inert in the demo, which would be an artefact of the generator rather
        # than of the model.
        primary = rng.choice(eligible)
        for j in range(n_past):
            vid += 1
            cid = primary if rng.random() < 0.75 else rng.choice(eligible)
            days_ago = rng.randint(10, 170)
            state.history.append(
                CompletedVisit(
                    visit_id=f"H{vid:04d}",
                    family_id=fid,
                    coordinator_id=cid,
                    when=now - timedelta(days=days_ago),
                    protocol=fam.protocol,
                    checkpoint=checkpoints[fam.protocol][min(j, 3)],
                    duration_hours=rng.choice([1.5, 2.0, 2.5, 3.0]),
                    travel_minutes=state.travel_minutes[(cid, fid)],
                )
            )

    # --- calendars ----------------------------------------------------------
    blocks: Dict[str, List[BusyBlock]] = {}
    for cid in ids:
        cal: List[BusyBlock] = []
        for day_offset in range(0, 21):
            day = (now + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if day.weekday() >= 5:
                continue
            for _ in range(rng.randint(0, 3)):
                start_hour = rng.choice([8, 9, 10, 11, 13, 14, 15])
                length = rng.choice([1, 1, 2])
                cal.append(
                    BusyBlock(
                        start=day + timedelta(hours=start_hour),
                        end=day + timedelta(hours=start_hour + length),
                        status=rng.choice(["busy", "busy", "busy", "tentative", "oof"]),
                    )
                )
        blocks[cid] = cal
        state.calendars[cid] = CalendarSnapshot(
            coordinator_id=cid,
            provider="mock",
            fetched_at=now - timedelta(seconds=rng.randint(30, 600)),
            blocks=cal,
        )
    # One coordinator's calendar is stale enough to make the assignment provisional.
    state.calendars["C06"].fetched_at = now - timedelta(minutes=40)
    state.demo_blocks = blocks  # type: ignore[attr-defined]

    # --- open visits --------------------------------------------------------
    visits: List[Visit] = []
    family_ids = sorted(state.families)
    # Sized to the roster: six working coordinators, two of them already nearly
    # full, and one part-time. Twenty open visits saturates that and the run
    # becomes a demonstration of the fairness vetoes rather than of the ranking.
    for i in range(16):
        fid = family_ids[i % len(family_ids)]
        fam = state.families[fid]
        start_offset = 2 + (i % 10)
        window_start = (now + timedelta(days=start_offset)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )
        # Home visits are weekday work. Layer 1 would filter a weekend window
        # down to the weekdays inside it anyway, but a queue that advertises a
        # Saturday visit reads as a scheduling error to anyone looking at it.
        while window_start.weekday() >= 5:
            window_start += timedelta(days=1)
        seq = checkpoints[fam.protocol]
        visits.append(
            apply_visit_duration(Visit(
                visit_id=f"V{i + 1:03d}",
                family_id=fid,
                protocol=fam.protocol,
                checkpoint=seq[min(1 + (i // len(family_ids)), len(seq) - 1)],
                window_start=window_start,
                window_end=window_start + timedelta(days=3, hours=9),
                duration_hours=rng.choice([1.5, 2.0, 2.5, 3.0]),
            ), fam)
        )
    return state, visits


def reference_case(now: datetime) -> Tuple[LabState, Visit]:
    """The hand-computable three-coordinator anchor used by the tests and the deck.

    Same inputs as the v2 worked example, so the two systems can be compared
    side by side. v2 ranked C, A, B. v3 ranks A, C, B, and the reason is the
    whole point of the rewrite: v2's pool-relative workload term handed A a zero
    and C a free half point, and its recency term rewarded B simply for having
    been idle.
    """
    state = LabState()
    specs = [
        ("A", 3, 5.0, 18.0, 30.0, True),
        ("B", 0, None, 9.0, 75.0, False),
        ("C", 1, 40.0, 12.0, 45.0, False),
    ]
    fam = Family(family_id="FREF", protocol="NICO", sigma=1)
    state.families["FREF"] = fam

    visit = Visit(
        visit_id="VREF",
        family_id="FREF",
        protocol="NICO",
        checkpoint="12mo",
        window_start=now + timedelta(days=3),
        window_end=now + timedelta(days=6),
        duration_hours=2.0,
    )
    for name, k, delta, hours, travel, did_prev in specs:
        state.coordinators[name] = Coordinator(
            coordinator_id=name,
            name=name,
            credentials={"ADOS", "CONSENT", "DRIVING"},
            capacity_hours_week=20.0,
            n_completed_visits=40,
            working_blocks=[(d, 8.0, 17.0) for d in range(5)],
        )
        state.committed_hours[name] = hours
        state.travel_minutes[(name, "FREF")] = travel
        state.calendars[name] = CalendarSnapshot(
            coordinator_id=name, provider="mock", fetched_at=now, blocks=[]
        )
        for i in range(k):
            state.history.append(
                CompletedVisit(
                    visit_id=f"HREF{name}{i}",
                    family_id="FREF",
                    coordinator_id=name,
                    when=now - timedelta(days=(delta or 0) + 30 * i),
                    protocol="NICO",
                    checkpoint="6mo" if (did_prev and i == 0) else "baseline",
                )
            )
    return state, visit
