"""Tests for the hard eligibility layer.

The rule these all defend: a person's competence is not negotiable against
their availability, their workload or their history with the family. If they
are not signed off on what the visit needs, no score may put them on it.

The staff here are synthetic on purpose. Testing the version-distinctness rule
needs somebody signed off on the 9-12m CSBS and not the 6m one, and nobody on
the real roster is in that position, so a test using real people would pass
without ever exercising the rule.

Run:  python3 tests/test_eligibility.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esd_scheduler.constraints import ReliabilityMatrix          # noqa: E402
from esd_scheduler.eligibility import ProfileConfig, evaluate    # noqa: E402

CONFIG = ProfileConfig.load()
NOW = datetime(2026, 8, 20, 9, 0)
CHART = ReliabilityMatrix.load()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


class Person:
    """A synthetic staff member with exactly the sign-offs given."""

    def __init__(self, pid, name, holds, roles=("clinician", "tech"),
                 solo=("1m", "12m")):
        self.id, self.name, self.holds = pid, name, set(holds)
        self.roles = list(roles)
        self.solo_from, self.solo_to = (solo if solo else (None, None))
        self.only_checkpoints = []


class Chart:
    """A reliability chart over synthetic people, with the real code list."""

    assessments = CHART.assessments

    def __init__(self, people):
        self.people = people

    def is_reliable(self, cid, assessment):
        person = self.people.get(cid)
        return bool(person and assessment in person.holds)


class Team:
    def __init__(self, people):
        self.people = people

    def by_id(self):
        return self.people

    def can_be_clinician_for(self, entry, checkpoint):
        if "clinician" not in entry.roles:
            return False
        if not entry.solo_from:
            return False
        order = ["1m", "2m", "3m", "6m", "9m", "12m", "24m", "36m", "48m"]
        if checkpoint not in order:
            return True
        return (order.index(entry.solo_from) <= order.index(checkpoint)
                <= order.index(entry.solo_to))


class Visit:
    def __init__(self, checkpoint, protocol="NANO", visit_id="V001"):
        self.visit_id, self.protocol, self.checkpoint = visit_id, protocol, checkpoint


def weigh(people, checkpoint, protocol="NANO"):
    lookup = {p.id: p for p in people}
    return evaluate(Visit(checkpoint, protocol), list(lookup),
                    Team(lookup), Chart(lookup), NOW, CONFIG)


# --------------------------------------------------------------------- tests

def test_one_missing_assessment_names_that_assessment():
    """The reason has to be specific enough to act on."""
    full = Person("P1", "Signed off on both", {"CSBS_9_12m", "Bayley_9_12m"})
    partial = Person("P2", "No Bayley", {"CSBS_9_12m"})
    result = weigh([full, partial], "9m")

    expect([v for v in result.clinicians if v.coordinator_id == "P1"],
           "somebody signed off on everything was not offered as clinician")
    blocked = [v for v in result.excluded + result.clinician_blocked
               if v.coordinator_id == "P2"]
    expect(blocked, "somebody missing an assessment was still offered")
    expect("Bayley" in blocked[0].reason,
           f"the reason does not name the missing assessment: {blocked[0].reason}")
    expect(blocked[0].failed_rule == "assessments",
           f"failed on {blocked[0].failed_rule}, expected assessments")


def test_the_6m_csbs_is_not_the_9m_csbs():
    """The rule most likely to put the wrong person in a family's house.

    CSBS Modified at 6m and standard CSBS at 9-12m are different assessments
    with different sign-offs. Somebody reliable on the second is not thereby
    able to run the first, and a system that treated a name like "CSBS
    trained" as one thing would let them.
    """
    person = Person("P3", "Standard CSBS only",
                    {"CSBS_9_12m", "Bayley_9_12m"})

    at_nine = weigh([person], "9m")
    expect(at_nine.clinicians,
           "somebody signed off on the 9-12m assessments was refused at 9m")

    at_six = weigh([person], "6m")
    expect(not at_six.clinicians,
           "the 9-12m CSBS was accepted as though it were the 6m one")
    reason = (at_six.excluded + at_six.clinician_blocked)[0].reason
    expect("Modified" in reason,
           f"the reason does not say which CSBS is missing: {reason}")


def test_nobody_eligible_is_said_plainly():
    """An empty pool is an answer. It must never become a weak recommendation."""
    nobody = Person("P4", "Signed off on nothing", set())
    result = weigh([nobody], "9m")
    expect(not result.clinicians, "an unqualified person was offered as clinician")
    expect(not result.has_anyone, "the result claims it can staff the visit")


def test_two_half_qualified_people_do_not_add_up_to_one_clinician():
    """The manual asks for one person who can run everything independently.

    Coverage is checked against the designated clinician alone. Allowing a
    pair to cover it between them would put a visit in front of a family with
    nobody able to finish it.
    """
    csbs_only = Person("P5", "CSBS only", {"CSBS_9_12m"})
    bayley_only = Person("P6", "Bayley only", {"Bayley_9_12m"})
    result = weigh([csbs_only, bayley_only], "9m")
    expect(not result.clinicians,
           "two partly-qualified people were allowed to cover a visit between them")


def test_somebody_who_can_tech_but_not_run_it_keeps_their_reason():
    """"Can tech, cannot run it" is an answer a scheduler asks for."""
    tech = Person("P7", "Tech only", {"CSBS_9_12m", "Bayley_9_12m"},
                  roles=("tech",))
    result = weigh([tech], "9m")
    expect([v for v in result.techs if v.coordinator_id == "P7"],
           "somebody who can tech was not offered the tech seat")
    expect([v for v in result.clinician_blocked if v.coordinator_id == "P7"],
           "the reason they cannot be the clinician was lost")


def test_a_remote_timepoint_asks_for_nobody():
    person = Person("P8", "Fully signed off", {"CSBS_9_12m", "Bayley_9_12m"})
    result = weigh([person], "24m")
    expect(result.remote, "the 24m timepoint is not reported as remote")
    expect(not result.clinicians and not result.techs,
           "a remote timepoint was staffed anyway")


def test_the_catalog_keeps_lookalike_assessments_apart():
    """The data has to carry the distinction, not the code."""
    expect("CSBS_MODIFIED_6M" in CONFIG.catalog, "the 6m CSBS is not catalogued")
    expect("CSBS_9_12M" in CONFIG.catalog, "the 9-12m CSBS is not catalogued")
    six = CONFIG.catalog["CSBS_MODIFIED_6M"]
    nine = CONFIG.catalog["CSBS_9_12M"]
    expect(six.code != nine.code, "the two CSBS entries share a code")
    expect(six.version != nine.version,
           "the two CSBS entries do not record different versions")


def test_an_alternative_group_is_satisfied_by_either_member():
    """A sibling evaluation needs ADOS plus Mullen OR DAS."""
    must, groups = CONFIG.required_assessments("SIBLING_EVAL_LAB")
    expect("ADOS_36M" in must, "the sibling evaluation does not require ADOS")
    expect(groups, "the Mullen-or-DAS alternative group is missing")
    combined = {a for group in groups for a in group}
    expect({"MULLEN_36M", "DAS_36M"} <= combined,
           f"the alternative group does not offer Mullen or DAS: {groups}")

    with_mullen = Person("P9", "ADOS and Mullen", {"ADOS", "Mullen"},
                         solo=("1m", "48m"))
    with_das = Person("PA", "ADOS and DAS", {"ADOS", "DAS_II"},
                      solo=("1m", "48m"))
    for person in (with_mullen, with_das):
        result = weigh([person], "sibling", protocol="Sibling Evaluation")
        expect(result.clinicians,
               f"{person.name} should satisfy ADOS plus one of Mullen or DAS")


def test_no_name_or_assessment_is_hard_coded_in_the_engine():
    """Rules are data. A rule in a conditional cannot be edited by the lab."""
    import re
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "esd_scheduler", "eligibility.py"),
            encoding="utf-8") as fh:
        source = fh.read()
    body = re.sub(r'"""..*?"""', "", source, flags=re.S)   # drop docstrings
    for name in ("Makenzie", "Sanjana", "Lauren", "Ramiro", "Sofia", "Maggie",
                 "Axie", "Bryson"):
        expect(name not in body,
               f"{name} is named in eligibility.py; rules belong in config")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
