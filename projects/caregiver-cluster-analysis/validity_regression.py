"""Executable, independently specified regression evidence for the validity audit.

Synthetic fixtures in Table 7 exercise production functions. They are never
survey observations and are never used for Table 8 trigger counts or accuracy.
The expected values below are fixed specifications, not copies of actual flags.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from numbers import Number
from typing import Any, Callable, Iterable

import pandas as pd

import refined_screening as screening
import refined_sources as sources


TEST_COLUMNS = [
    "Test ID", "Rule ID", "Test type", "Study PID", "Study name",
    "Input condition", "Expected result", "Actual result", "Pass or fail",
    "Failure explanation", "Code correction", "Retest result", "Evidence origin",
    "Test applicability", "Applicability rationale",
]
PERFORMANCE_COLUMNS = [
    "Rule ID", "Rule name", "Study PID", "Study name", "Records evaluated",
    "Records triggered", "Trigger percentage", "Confirmed correct triggers",
    "False positives", "False negatives", "Unable to adjudicate",
    "Estimated precision", "Estimated recall", "Regression-test status",
    "Recommendation", "Denominator notes",
]
REQUIRED_TEST_TYPES = {
    "Positive test", "Negative test", "Missing-value test", "Capitalization test",
    "Whitespace test", "Boundary-value test", "Conflicting-value test",
    "Study-specific test", "Previously misclassified record test", "Non-trigger protection test",
}


@dataclass(frozen=True)
class RegressionCase:
    """One explicit expectation against an executable production operation."""

    test_id: str
    rule_id: str
    test_type: str
    input_condition: str
    expected: Any
    execute: Callable[[], Any]
    study_pid: int | str = 4797
    study_name: str = "Synthetic regression fixture; not a survey respondent"
    evidence_origin: str = "Synthetic regression fixture; not an observed survey result"


def _json(value: Any) -> str:
    def scalar(item):
        if hasattr(item, "item"):
            return item.item()
        raise TypeError(f"Unsupported result type: {type(item).__name__}")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=scalar)


def _same_result(expected: Any, actual: Any) -> bool:
    """Numeric int/float storage is immaterial; booleans are not numeric flags."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_same_result(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_same_result(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, Number) and isinstance(actual, Number):
        return expected == actual
    return type(expected) is type(actual) and expected == actual


def _field(name, kind="radio", choices="", form="eligibility", **extra):
    return {"field_name": name, "field_type": kind, "form_name": form,
            "select_choices_or_calculations": choices, "branching_logic": "",
            "field_annotation": "", **extra}


def fixture_metadata(attention=True):
    """Fictitious code positions deliberately differ from live-study positions."""
    fields = [
        _field("pronouns_elig", choices="9, He/Him/His | 8, She/Her/Hers | 7, They/Them/Theirs"),
        _field("demo_gender", choices="6, Man | 5, Woman | 4, Non-binary", form="demographics"),
        _field("fif_child_needs", "yesno", form="family_information_form"),
        _field("fif_num_autistic", choices="0, 0 | 1, 1 | 2, 2 | 9, More than 3", form="family_information_form"),
        _field("fif_num_children", choices="1, 1 | 2, 2 | 8, 8+", form="family_information_form"),
        _field("age_confirm_elig", "text"),
        _field("demo_country", "yesno", choices="1, US\n2, Canada\n3, Other", form="demographics"),
        _field("tfa_comments", "notes", form="tfa"),
    ]
    if attention:
        fields.append(_field("elig_knee", "checkbox", "3, Puts on a Band-Aid | 7, Flips on a light | 8, There is no image | 9, I prefer not to answer"))
    return pd.DataFrame(fields)


def fixture_response(record_id="1", **overrides):
    row = {
        "record_id": record_id, **{name: "2" for name in screening.COMPLETION_FIELDS},
        "get_time_fif": "4", "get_time_val": "1", "get_time_tfa": "10", "get_time_demo": "2",
        "demo_email": f"synthetic{record_id}@example.invalid",
        "eligibility_timestamp": "2026-09-24 12:00:00", "pronouns_elig": "8", "demo_gender": "5",
        "fif_child_needs": "1", "fif_num_autistic": "1", "fif_num_children": "2",
        "age_confirm_elig": "35", "demo_country": "1", "zip_demo": "29201",
        "elig_knee___3": "1", "elig_knee___7": "0", "elig_knee___8": "0", "elig_knee___9": "0",
        "tfa_comments": "",
    }
    return {**row, **overrides}


def _score(rows=None, metadata=None, pid=4797, verified=True):
    return screening.score_study(pd.DataFrame(rows or [fixture_response()]),
        f"Synthetic regression fixture (PID {pid})", fixture_metadata() if metadata is None else metadata,
        project_id=pid, provenance_verified=verified)


def _scales(n=20):
    fields = [_field(f"rating_{i}", choices="1, Not at all like me | 2, Somewhat like me | 3, Like me | 4, Very much like me",
                     form="values") for i in range(n)]
    return pd.concat([fixture_metadata(), pd.DataFrame(fields)], ignore_index=True)


def _filter_flag(pid, rid, text="", which="contains_test_word", kind="notes", spanish=""):
    frame = pd.DataFrame([{"record_id": str(rid), "comment": text, "comment_s": spanish}])
    meta = pd.DataFrame([_field("comment", kind), _field("comment_s", "notes")])
    kept, excluded = sources.filter_source_records(frame, meta, pid)
    return {"triggered": bool(not excluded.empty and excluded[which].iloc[0]),
            "retained": len(kept) == 1}


def _raises_value_error(operation):
    try:
        operation()
    except ValueError:
        return "Rejected with ValueError"
    return "Accepted"


def build_regression_cases() -> list[RegressionCase]:
    """Readable boundary specifications for every legacy rule and active gate."""
    cases: list[RegressionCase] = []
    counters: dict[str, int] = {}

    def add(rule, kind, description, expected, execute, pid=4797):
        counters[rule] = counters.get(rule, 0) + 1
        cases.append(RegressionCase(f"{rule}-{counters[rule]:03}", rule, kind,
            description, expected, execute, study_pid=pid))

    def scored(rule, kind, description, overrides=None, expected=1, *, rows=None,
               meta=None, pid=4797, columns=None, verified=True):
        # Capture independent values now; the invocation is deferred to execution.
        inputs = rows if rows is not None else [fixture_response(**(overrides or {}))]
        wanted = columns or [rule]
        desired = expected if isinstance(expected, dict) else {rule: expected}
        def evaluate():
            result = _score(inputs, meta, pid, verified)
            return {name: result.iloc[0][name] for name in wanted}
        add(rule, kind, description, desired, evaluate, pid)

    for rule, field, boundary in [("R1", "get_time_tfa", "4.57"), ("R2", "get_time_tfa", "7.85")]:
        below = "4.569999" if rule == "R1" else "7.849999"
        scored(rule, "Positive test", "All four nonnegative sections; measured duration just below the strict floor.", {field: below})
        scored(rule, "Negative test", "Durations above configured floor.", expected=0)
        scored(rule, "Boundary-value test", "Duration exactly equals configured floor; equality is not fast.", {field: boundary}, 0)
        scored(rule, "Missing-value test", "Required timing value is blank; missing timing is not a rapid-completion flag.", {field: ""},
               {rule: 0, f"{rule} status": "Not evaluable"}, columns=[rule, f"{rule} status"])
        scored(rule, "Conflicting-value test", "Negative duration is invalid timing, not rapid completion.", {field: "-1"},
               {rule: 0, "Timing data issue": 1}, columns=[rule, "Timing data issue"])
        scored(rule, "Whitespace test", "Numeric duration padded with whitespace still compares numerically.", {field: f"  {below}  "})
    scored("R3", "Positive test", "A family-information duration below 2.55 minutes triggers a section flag.", {"get_time_fif": "2.54"})
    scored("R3", "Negative test", "All four section times exceed their floors.", expected=0)
    scored("R3", "Boundary-value test", "Every section is exactly at its own floor; none is strictly below.",
           {"get_time_fif": "2.55", "get_time_val": ".22", "get_time_tfa": "6.67", "get_time_demo": ".95"}, 0)
    scored("R3", "Missing-value test", "All section durations blank yields not evaluable, not flagged.",
           {f"get_time_{key}": "" for key in screening.SECTION_FLOORS_MIN},
           {"R3": 0, "R3 status": "Not evaluable"}, columns=["R3", "R3 status"])
    scored("R3", "Non-trigger protection test", "One short supporting section alone is not a new independent evidence family.",
           {"get_time_fif": "2.54"}, {"R3": 1, "Independent evidence families": 0, "Recommended action": "Pay now"},
           columns=["R3", "Independent evidence families", "Recommended action"])

    vector = {f"rating_{i}": str(i % 4 + 1) for i in range(20)}
    flat = {f"rating_{i}": "2" for i in range(20)}
    scored("R4", "Positive test", "Twenty identical valid ratings in a comparable block have SD zero.", flat, meta=_scales())
    scored("R4", "Negative test", "Varied comparable ratings do not straightline.", vector, 0, meta=_scales())
    scored("R4", "Missing-value test", "Blank ratings cannot establish straightlining.", expected=0, meta=_scales())
    scored("R4", "Boundary-value test", "Exactly 80 percent of 20 ratings observed and all equal remains evaluable.",
           {**flat, **{f"rating_{i}": "" for i in range(4)}}, meta=_scales())
    scored("R4", "Non-trigger protection test", "75 percent coverage is below required 80 percent even when identical.",
           {**flat, **{f"rating_{i}": "" for i in range(5)}}, 0, meta=_scales())
    scored("R4", "Conflicting-value test", "Out-of-range scale code 9 is missing for coverage and not a valid flat rating.",
           {f"rating_{i}": "9" for i in range(20)}, 0, meta=_scales())
    scored("R5", "Positive test", "Two same-study records share all 20 varied scale answers.",
           rows=[fixture_response("1", **vector), fixture_response("2", **vector)], meta=_scales())
    scored("R5", "Negative test", "One differing valid scale answer breaks exact fingerprint equality.", expected=0,
           rows=[fixture_response("1", **vector), fixture_response("2", **{**vector, "rating_0": "4"})], meta=_scales())
    scored("R5", "Missing-value test", "No rating data cannot identify duplicate responses.", expected=0, meta=_scales())
    scored("R5", "Boundary-value test", "Exact matching 19-item scales are below the required 20 metadata items.", expected=0,
           rows=[fixture_response("1", **vector), fixture_response("2", **vector)], meta=_scales(19))
    partial = {**vector, **{f"rating_{i}": "" for i in range(4)}}
    scored("R5", "Boundary-value test", "Exactly 16 of 20 matching ratings reaches 80 percent coverage.",
           rows=[fixture_response("1", **partial), fixture_response("2", **partial)], meta=_scales())
    partial = {**vector, **{f"rating_{i}": "" for i in range(5)}}
    scored("R5", "Non-trigger protection test", "Matching vectors with 75 percent coverage are not duplicate flags.", expected=0,
           rows=[fixture_response("1", **partial), fixture_response("2", **partial)], meta=_scales())
    add("R5", "Study-specific test", "Equal answers in separate PIDs do not create a within-study duplicate.", [0, 0],
        lambda: [_score([fixture_response(**vector)], _scales(), pid).iloc[0].R5 for pid in (4797, 4931)], "Multiple")

    scored("R6", "Positive test", "Three distinct respondents arrive at the same time; each has two neighbours.",
           rows=[fixture_response(str(i)) for i in range(3)])
    scored("R6", "Negative test", "Two nearby records supply only one neighbour each.", expected=0,
           rows=[fixture_response("1"), fixture_response("2")])
    scored("R6", "Missing-value test", "Missing arrival timestamp is not a burst.", {"eligibility_timestamp": ""}, 0)
    scored("R6", "Boundary-value test", "Neighbours at exactly minus and plus 120 seconds count inclusively.",
           rows=[fixture_response("1"), fixture_response("2", eligibility_timestamp="2026-09-24 11:58:00"),
                 fixture_response("3", eligibility_timestamp="2026-09-24 12:02:00")])
    scored("R6", "Non-trigger protection test", "Two neighbours at 121 seconds fall outside the burst window.", expected=0,
           rows=[fixture_response("1"), fixture_response("2", eligibility_timestamp="2026-09-24 11:57:59"),
                 fixture_response("3", eligibility_timestamp="2026-09-24 12:02:01")])
    scored("R6", "Non-trigger protection test", "Recruitment burst alone does not block payment.",
           rows=[fixture_response(str(i)) for i in range(3)], expected={"R6": 1, "Recommended action": "Pay now"},
           columns=["R6", "Recommended action"])

    narrative = "Our family carefully considered the practical benefits and challenges of early screening because accessible support services and clear communication would help us make informed decisions together."
    scored("R7", "Positive test", "Two substantive narratives with at least 100 characters and 20 words are identical.",
           rows=[fixture_response("1", tfa_comments=narrative), fixture_response("2", tfa_comments=narrative)])
    scored("R7", "Capitalization test", "Case differences do not prevent an otherwise identical substantive narrative match.",
           rows=[fixture_response("1", tfa_comments=narrative.upper()), fixture_response("2", tfa_comments=narrative.lower())])
    scored("R7", "Whitespace test", "Extra spaces do not prevent substantive narrative matching.",
           rows=[fixture_response("1", tfa_comments="  " + narrative.replace(" ", "  ") + "  "), fixture_response("2", tfa_comments=narrative)])
    scored("R7", "Missing-value test", "Missing comments are not copied narratives.", expected=0)
    scored("R7", "Negative test", "One substantive narrative with no comparison record cannot be copied.", {"tfa_comments": narrative}, 0)
    scored("R7", "Non-trigger protection test", "Repeated brief but plausible comments are not treated as copied substantive text.", expected=0,
           rows=[fixture_response("1", tfa_comments="No concerns"), fixture_response("2", tfa_comments="No concerns")])

    scored("R8", "Positive test", "At least four autistic children exceeds an exact total of one child.", {"fif_num_autistic": "9", "fif_num_children": "1"})
    scored("R8", "Negative test", "One autistic child within two total children is consistent.", expected=0)
    scored("R8", "Missing-value test", "Unknown total and autistic-child counts cannot establish count contradiction.",
           {"fif_num_autistic": "", "fif_num_children": ""}, {"R8": 0, "R8 status": "Not evaluable"}, columns=["R8", "R8 status"])
    scored("R8", "Boundary-value test", "Autistic count equal to total is valid.", {"fif_num_autistic": "2", "fif_num_children": "2"}, 0)
    scored("R8", "Non-trigger protection test", "More than three autistic children and total eight-or-more are overlapping bounds.",
           {"fif_num_autistic": "9", "fif_num_children": "8"}, 0)
    scored("R8", "Whitespace test", "Whitespace and numeric-string codes are decoded through choice labels.", {"fif_num_autistic": " 9.0 ", "fif_num_children": " 1 "})
    branch_meta = pd.concat([fixture_metadata(), pd.DataFrame([
        _field("follow", "checkbox", "1, Yes | 2, No", form="family_information_form", branching_logic="[fif_num_autistic] > 0"),
        _field("alternative", "notes", form="family_information_form", branching_logic="[fif_num_autistic] > 0 or [pregnant] = 1"),
    ])], ignore_index=True)
    scored("R8", "Conflicting-value test", "A stored gated answer at zero autistic children is review context, not independent count evidence.",
           {"fif_num_autistic": "0", "follow___1": "1"}, {"R8": 1, "Logical evidence": 0}, meta=branch_meta, columns=["R8", "Logical evidence"])
    scored("R8", "Non-trigger protection test", "Unselected checkbox zeros and a legitimate alternative OR path are not contradictions.",
           {"fif_num_autistic": "0", "follow___1": "0", "follow___2": "0", "alternative": "visible response"}, 0, meta=branch_meta)

    for age, expected in [("17", 1), ("18", 0), ("100", 0), ("101", 1)]:
        scored("R9", "Boundary-value test", f"Reported age {age}; accepted age interval is inclusive 18 through 100.", {"age_confirm_elig": age}, expected)
    scored("R9", "Positive test", "Explicit US residence with malformed nonempty ZIP requires review.", {"zip_demo": "invalid"})
    scored("R9", "Negative test", "Ordinary age and valid US ZIP are clear.", expected=0)
    scored("R9", "Missing-value test", "Missing age and ZIP cannot themselves create a demographic contradiction.",
           {"age_confirm_elig": "", "zip_demo": ""}, {"R9": 0, "R9 status": "Not evaluable"}, columns=["R9", "R9 status"])
    scored("R9", "Whitespace test", "Trimmed valid numeric age and ZIP remain valid.", {"age_confirm_elig": " 35 ", "zip_demo": " 29201 "}, 0)
    scored("R9", "Non-trigger protection test", "Foreign postal formatting is permitted for an explicit non-US response.", {"demo_country": "0", "zip_demo": "SW1A 1AA"}, 0)
    age_meta = fixture_metadata().loc[lambda frame: frame.field_name.ne("age_confirm_elig")]
    scored("R9", "Study-specific test", "When numeric eligibility age is absent, exact age uses birth date and survey date, not a yes/no code.",
           {"age_elig": "1", "demo_momdob": "2009-09-25"}, {"R9": 1, "Reported caregiver age": 16}, meta=age_meta, pid=4931,
           columns=["R9", "Reported caregiver age"])

    scored("R10", "Positive test", "A configured temporary-email domain creates a contact signal.", {"demo_email": "synthetic@mailinator.com"})
    scored("R10", "Negative test", "A normal unlisted email domain does not trigger the temporary-domain list.", expected=0)
    scored("R10", "Missing-value test", "Missing email is not a temporary-domain match.", {"demo_email": ""},
           {"R10": 0, "R10 status": "Not evaluable"}, columns=["R10", "R10 status"])
    scored("R10", "Capitalization test", "Domain matching ignores capitalization.", {"demo_email": "SYNTHETIC@MAILINATOR.COM"})
    scored("R10", "Whitespace test", "Outer whitespace is trimmed before domain comparison.", {"demo_email": "  synthetic@mailinator.com  "})
    scored("R10", "Non-trigger protection test", "An address whose domain merely contains the listed domain is not an exact list match.",
           {"demo_email": "synthetic@mailinator.com.example.invalid"}, 0)
    scored("R11", "Positive test", "Completed response with neither demographics nor eligibility email is flagged.", {"demo_email": ""})
    scored("R11", "Negative test", "Completed response with email is clear.", expected=0)
    scored("R11", "Missing-value test", "Missing completion status does not pretend the whole survey was completed.",
           {"demo_email": "", "tfa_complete": ""}, {"R11": 0, "R11 status": "Not evaluable"}, columns=["R11", "R11 status"])
    scored("R11", "Whitespace test", "Whitespace-only emails count as missing.", {"demo_email": " \t "})
    scored("R11", "Non-trigger protection test", "A usable eligibility email supplies a valid fallback when demographics email is missing.",
           {"demo_email": "", "email_elig": "synthetic@example.invalid"}, 0)
    for stamp, expected in [("00:00:00", 1), ("04:59:59", 1), ("05:00:00", 0), ("23:59:59", 0)]:
        scored("R12", "Boundary-value test", f"Local start time {stamp}; overnight context is midnight through 04:59:59.",
               {"eligibility_timestamp": f"2026-09-24 {stamp}"}, expected)
    scored("R12", "Missing-value test", "Missing start time cannot trigger overnight context.", {"eligibility_timestamp": ""},
           {"R12": 0, "R12 status": "Not evaluable"}, columns=["R12", "R12 status"])
    scored("R12", "Non-trigger protection test", "An overnight response alone does not block a clear complete response.",
           {"eligibility_timestamp": "2026-09-24 02:00:00"}, {"R12": 1, "Recommended action": "Pay now"}, columns=["R12", "Recommended action"])

    scored("R13", "Positive test", "Light alone is an answered but incorrect attention option.", {"elig_knee___3": "0", "elig_knee___7": "1"})
    scored("R13", "Negative test", "The metadata-defined Band-Aid code is 3, not a hard-coded code 2.", expected=0)
    scored("R13", "Missing-value test", "An unanswered checkbox group is held for review, not treated as answered wrong.",
           {"elig_knee___3": "0"}, {"R13": 0, "R13 status": "Not evaluable", "Recommended action": "Check by hand"},
           columns=["R13", "R13 status", "Recommended action"])
    scored("R13", "Conflicting-value test", "Band-Aid plus an additional incorrect option triggers attention review.", {"elig_knee___7": "1"})
    capmeta = fixture_metadata()
    capmeta.loc[capmeta.field_name.eq("elig_knee"), "select_choices_or_calculations"] = "3, PUTS ON A BAND AID | 7, FLIPS ON A LIGHT | 8, THERE IS NO IMAGE | 9, I PREFER NOT TO ANSWER"
    scored("R13", "Capitalization test", "Metadata labels tolerate capitalization and the Band Aid spacing variant.", expected=0, meta=capmeta)
    scored("R13", "Whitespace test", "Whitespace around a stored selected checkbox code remains a valid Band-Aid answer.", {"elig_knee___3": " 1 "}, 0)
    scored("R13", "Study-specific test", "A study without this item does not acquire a missing-attention payment hold.", expected={"R13": 0, "R13 status": "Not applicable", "Recommended action": "Pay now"},
           meta=fixture_metadata(attention=False), pid=4581, columns=["R13", "R13 status", "Recommended action"])
    scored("R13", "Non-trigger protection test", "No-image response is a review signal, not independent attention-failure evidence.",
           {"elig_knee___3": "0", "elig_knee___8": "1"}, {"R13": 1, "Attention evidence": 0, "Recommended action": "Check by hand"},
           columns=["R13", "Attention evidence", "Recommended action"])

    scored("R14", "Positive test", "She pronouns and man gender are recorded as context without blocking payment.", {"demo_gender": "6"},
           {"R14": 1, "Recommended action": "Pay now", "Independent evidence families": 0}, columns=["R14", "Recommended action", "Independent evidence families"])
    scored("R14", "Negative test", "She pronouns and woman gender do not trigger the specified pairing.", expected=0)
    scored("R14", "Missing-value test", "Missing pronouns cannot establish an identity pairing.", {"pronouns_elig": ""},
           {"R14": 0, "R14 status": "Not evaluable"}, columns=["R14", "R14 status"])
    scored("R14", "Whitespace test", "Choice codes with whitespace and decimal-string notation are normalized.", {"pronouns_elig": " 8.0 ", "demo_gender": " 6.0 "})
    scored("R14", "Non-trigger protection test", "They pronouns and man gender remain a plausible self-description.", {"pronouns_elig": "7", "demo_gender": "6"}, 0)
    spanish_meta = fixture_metadata()
    spanish_meta.loc[spanish_meta.field_name.eq("pronouns_elig"), "select_choices_or_calculations"] = "9, Él | 8, Ella | 7, Elle"
    spanish_meta.loc[spanish_meta.field_name.eq("demo_gender"), "select_choices_or_calculations"] = "6, Hombre | 5, Mujer | 4, No binario"
    scored("R14", "Study-specific test", "Spanish label meanings drive the same context-only pairing.", {"demo_gender": "6"}, meta=spanish_meta, pid=5749)
    for needs, count, expected, kind in [("0", "1", 1, "Positive test"), ("1", "1", 0, "Negative test"),
                                         ("", "1", 0, "Missing-value test"), ("0", "0", 0, "Boundary-value test"),
                                         ("0", "", 0, "Missing-value test")]:
        scored("R15", kind, f"Explicit disability code {needs!r} and autistic-child count code {count!r}; missing is not explicit No.",
               {"fif_child_needs": needs, "fif_num_autistic": count}, expected)
    scored("R15", "Whitespace test", "Trimmed Yes/No and count codes compare through metadata meaning.", {"fif_child_needs": " 0.0 ", "fif_num_autistic": " 1.0 "})
    scored("R15", "Non-trigger protection test", "Disability wording disagreement alone requires clarification and supplies no independent fraud family.",
           {"fif_child_needs": "0"}, {"R15": 1, "Independent evidence families": 0, "Recommended action": "Check by hand"},
           columns=["R15", "Independent evidence families", "Recommended action"])

    for value, expected, kind, description in [
        ("test", True, "Positive test", "Standalone test keyword in free text."),
        ("TEST", True, "Capitalization test", "Standalone uppercase keyword."),
        ("  test  ", True, "Whitespace test", "Standalone keyword with outer whitespace."),
        ("test!", True, "Boundary-value test", "Punctuation delimits a standalone keyword."),
        ("contest testing latest", False, "Non-trigger protection test", "Unrelated words contain only the substring."),
        ("", False, "Missing-value test", "Blank free text contains no keyword."),
        ("caregiving", False, "Negative test", "Ordinary response contains no keyword."),
        ("a clinical test was helpful", True, "Conflicting-value test", "Literal approved keyword policy also matches legitimate clinical-test mentions; false-positive risk documented."),
    ]:
        add("EX_TEST_WORD", kind, description, {"triggered": expected, "retained": not expected},
            lambda value=value: _filter_flag(4797, "1", value))
    add("EX_TEST_WORD", "Study-specific test", "Spanish source field is scanned before language folding.",
        {"triggered": True, "retained": False}, lambda: _filter_flag(5749, "474", spanish="test"), 5749)
    add("EX_TEST_WORD", "Non-trigger protection test", "A coded radio value is not a free-text field.",
        {"triggered": False, "retained": True}, lambda: _filter_flag(4797, "1", "test", kind="radio"))
    for rule, flag, low, high in [("EX_ARCHIVE_5749", "archive_clone", 1, 445), ("EX_PRELAUNCH_5749", "prelaunch_protocol", 446, 473)]:
        for rid in (low, high):
            add(rule, "Boundary-value test", f"PID 5749 record {rid} is within inclusive protocol interval {low}–{high}.",
                {"triggered": True, "retained": False}, lambda rid=rid, flag=flag: _filter_flag(5749, rid, which=flag), 5749)
        add(rule, "Negative test", "PID 5749 record 474 is outside archived and prelaunch intervals.",
            {"triggered": False, "retained": True}, lambda flag=flag: _filter_flag(5749, 474, which=flag), 5749)
        add(rule, "Study-specific test", "Same numeric record ID in another study is not covered by PID 5749's exclusion.",
            {"triggered": False, "retained": True}, lambda flag=flag, low=low: _filter_flag(4797, low, which=flag), 4797)
        add(rule, "Missing-value test", "A blank PID 5749 record ID cannot be assigned to a study-specific interval; partitioning rejects it.",
            "Rejected with ValueError", lambda flag=flag: _raises_value_error(lambda: _filter_flag(5749, "", which=flag)), 5749)
    add("EX_TEST_WORD", "Missing-value test", "A text field declared in metadata but omitted from the export cannot be silently treated as keyword-free.",
        "Rejected with ValueError", lambda: _raises_value_error(lambda: sources.filter_source_records(
            pd.DataFrame([{"record_id": "1"}]), pd.DataFrame([_field("comment", "notes")]), 4797)))

    for complete, independent, review, contact, source_issue, expected, kind in [
        (True, 0, False, False, False, "Pay now", "Negative test"),
        (True, 0, False, False, True, "Check by hand", "Positive test"),
        (True, 2, True, False, True, "Check by hand", "Conflicting-value test"),
    ]:
        add("G_SOURCE", kind, "Source provenance/mapping uncertainty blocks clearance and overrides an integrity refusal recommendation.", expected,
            lambda c=complete, n=independent, r=review, h=contact, s=source_issue: screening.decision_gate(c, n, r, h, s))
    scored("G_SOURCE", "Study-specific test", "Unverified live-project identity cannot yield Pay now even with otherwise clean responses.",
           expected={"Source mapping issue": 1, "Recommended action": "Check by hand"}, columns=["Source mapping issue", "Recommended action"], verified=False)
    for wrong, description in [
        ({"project_id": 4797, "project_title": "Fixture Bilingual"}, "Token project PID differs from the configured study PID."),
        ({"project_id": 5749, "project_title": "Another title"}, "Token project title differs from the study-reference title."),
        ({"project_id": 5749, "project_title": "Fixture Bilingual", "has_repeating_instruments_or_events": 1}, "Repeating structure conflicts with one-respondent-row assumption."),
    ]:
        add("G_SOURCE", "Conflicting-value test", description, "Rejected with ValueError",
            lambda wrong=wrong: _raises_value_error(lambda: sources.validate_project_identity(
                wrong, {"project_id": 5749, "study_name": "Fixture Bilingual"})), 5749)
    bilingual_meta = pd.DataFrame([
        _field("english_spanish", "radio", "1, Español | 2, English"),
        _field("elig_knee", "checkbox", "1, Light | 2, Band-Aid"),
        _field("elig_knee_s", "checkbox", "1, Luz | 2, Curita"),
    ])
    def folded_attention(conflict=False):
        raw = pd.DataFrame([{"record_id": "474", "english_spanish": "1", "elig_knee___1": "1" if conflict else "0",
                             "elig_knee___2": "0", "elig_knee_s___1": "0", "elig_knee_s___2": "1"}])
        folded, _, _, _ = sources.fold_verified_twins(raw, bilingual_meta, 5749)
        return {"Band-Aid selected": folded.loc[0, "elig_knee___2"], "Mapping conflict": bool(folded.loc[0, "source_mapping_issue"])}
    add("G_SOURCE", "Study-specific test", "Spanish selected Band-Aid survives English checkbox zeros before canonical scoring.",
        {"Band-Aid selected": "1", "Mapping conflict": False}, folded_attention, 5749)
    add("G_SOURCE", "Conflicting-value test", "Contradictory nonempty English and Spanish selections retain the selected-language response and require mapping review.",
        {"Band-Aid selected": "1", "Mapping conflict": True}, lambda: folded_attention(True), 5749)
    for value, expected, kind in [("0", "Incomplete - not eligible", "Positive test"), ("", "Incomplete - not eligible", "Missing-value test"),
                                  ("2", "Pay now", "Negative test"), (" 2 ", "Pay now", "Whitespace test")]:
        scored("G_COMPLETE", kind, "All four required sections must carry completion status 2.", {"tfa_complete": value},
               {"Recommended action": expected}, columns=["Recommended action"])
    scored("G_COMPLETE", "Conflicting-value test", "Incomplete survey remains unpaid even when other integrity concerns are present.",
           {"tfa_complete": "0", "get_time_tfa": "1", "elig_knee___7": "1"}, {"Recommended action": "Incomplete - not eligible"}, columns=["Recommended action"])
    for overrides, hold, description, kind in [
        ({"demo_email": ""}, 1, "Missing payment contact.", "Missing-value test"),
        ({"demo_email": "not-an-address"}, 1, "Malformed payment contact.", "Positive test"),
        ({"demo_email_confirm": "different@example.invalid"}, 1, "Confirmation conflicts with supplied contact.", "Conflicting-value test"),
        ({}, 0, "Usable unique ordinary contact.", "Negative test"),
        ({"demo_email": "  SYNTHETIC1@EXAMPLE.INVALID "}, 0, "Case and outer-space normalization preserve usable contact.", "Whitespace test"),
    ]:
        scored("G_CONTACT", kind, description, overrides,
               {"Payment contact hold": hold, "Recommended action": "Check by hand" if hold else "Pay now"}, columns=["Payment contact hold", "Recommended action"])
    scored("G_CONTACT", "Positive test", "Shared payment contact is held while record IDs remain separate.",
           rows=[fixture_response("1"), fixture_response("2", demo_email="synthetic1@example.invalid")],
           expected={"Responses sharing payment email": 2, "Payment contact hold": 1, "Recommended action": "Check by hand"},
           columns=["Responses sharing payment email", "Payment contact hold", "Recommended action"])
    def across_study_contact():
        from types import SimpleNamespace
        fixture_sources = SimpleNamespace(
            records_by_pid={pid: pd.DataFrame([fixture_response()]) for pid in (4797, 4931)},
            metadata_by_pid={pid: fixture_metadata() for pid in (4797, 4931)},
            registry={pid: {"display_label": f"Synthetic {pid}", "study_name": f"Synthetic {pid}", "population": "Synthetic"}
                      for pid in (4797, 4931)})
        result = screening.score_refined_records(fixture_sources)
        return {"Unique response keys": bool(result["Response key"].is_unique),
                "Shared contact counts": result["Responses sharing payment email"].tolist(),
                "Actions": result["Recommended action"].tolist()}
    add("G_CONTACT", "Study-specific test", "A shared contact across separate studies holds both records without merging equal record IDs.",
        {"Unique response keys": True, "Shared contact counts": [2, 2], "Actions": ["Check by hand", "Check by hand"]},
        across_study_contact, "Multiple")
    scored("G_INDEPENDENT", "Positive test", "Independent timing and attention concerns reach the documented combination gate.",
           {"get_time_tfa": "1", "elig_knee___3": "0", "elig_knee___7": "1"},
           {"Independent evidence families": 2, "Recommended action": "Do not pay"}, columns=["Independent evidence families", "Recommended action"])
    scored("G_INDEPENDENT", "Negative test", "Clean response has no independent evidence family.", expected={"Independent evidence families": 0, "Recommended action": "Pay now"},
           columns=["Independent evidence families", "Recommended action"])
    scored("G_INDEPENDENT", "Boundary-value test", "One independent timing family requires review, not automatic integrity refusal.",
           {"get_time_tfa": "1"}, {"Independent evidence families": 1, "Recommended action": "Check by hand"}, columns=["Independent evidence families", "Recommended action"])
    scored("G_INDEPENDENT", "Non-trigger protection test", "Three overlapping timing flags still supply only one independent family.",
           {"get_time_fif": "1", "get_time_val": ".1", "get_time_tfa": "1", "get_time_demo": ".1"},
           {"R1": 1, "R2": 1, "R3": 1, "Independent evidence families": 1, "Recommended action": "Check by hand"},
           columns=["R1", "R2", "R3", "Independent evidence families", "Recommended action"])

    # Additional normalization and conflict cases are assigned to their own
    # specification, rather than assuming another rule's passing flag proves it.
    for rule in ("R1", "R2"):
        scored(rule, "Non-trigger protection test", "A single short section does not make adequate total/TFA timing fail this rule.",
               {"get_time_fif": "1"}, 0)
    scored("R3", "Whitespace test", "Whitespace around numeric section durations is ignored.", {"get_time_fif": " 2.54 "})
    scored("R3", "Conflicting-value test", "One negative duration is invalid but a separate short valid section still triggers R3.",
           {"get_time_fif": "-1", "get_time_val": ".1"}, {"R3": 1, "Timing data issue": 1}, columns=["R3", "Timing data issue"])
    scored("R4", "Whitespace test", "Trimmed numeric scale codes retain their variance.", {key: f" {value} " for key, value in flat.items()}, meta=_scales())
    scale_caps = _scales()
    scale_caps["select_choices_or_calculations"] = scale_caps["select_choices_or_calculations"].str.upper()
    scored("R4", "Capitalization test", "Uppercase ordinal labels still identify eligible rating scales.", flat, meta=scale_caps)
    scale_nonrating = _scales()
    scale_nonrating.loc[scale_nonrating.field_name.str.startswith("rating_"), "form_name"] = "eligibility"
    scored("R4", "Study-specific test", "A study with similarly coded eligibility items does not treat them as Values/TFA scales.",
           flat, 0, meta=scale_nonrating, pid=4581)
    padded_vector = {key: f" {value} " for key, value in vector.items()}
    scored("R5", "Whitespace test", "Whitespace around stored numeric codes does not break the exact normalized rating pattern.",
           rows=[fixture_response("1", **padded_vector), fixture_response("2", **vector)], meta=_scales())
    scored("R5", "Capitalization test", "Uppercase ordinal scale labels retain the metadata-defined fingerprint fields.",
           rows=[fixture_response("1", **vector), fixture_response("2", **vector)], meta=scale_caps)
    scored("R5", "Conflicting-value test", "A matching invalid code does not count toward the required valid-answer coverage.", expected=0,
           rows=[fixture_response("1", **{**vector, **{f"rating_{i}": "9" for i in range(5)}}),
                 fixture_response("2", **{**vector, **{f"rating_{i}": "9" for i in range(5)}})], meta=_scales())
    scored("R6", "Whitespace test", "Trimmed timestamp strings still produce matching arrival times.",
           rows=[fixture_response(str(i), eligibility_timestamp=" 2026-09-24 12:00:00 ") for i in range(3)])
    scored("R6", "Conflicting-value test", "Two valid arrivals plus one malformed timestamp do not supply two valid neighbours.", expected=0,
           rows=[fixture_response("1"), fixture_response("2"), fixture_response("3", eligibility_timestamp="not-a-date")])
    add("R6", "Study-specific test", "Two arrivals in one study and one arrival in a second study never form one three-person burst.", [0, 0],
        lambda: [_score([fixture_response("1"), fixture_response("2")], pid=4797).iloc[0].R6,
                 _score([fixture_response("1")], pid=4931).iloc[0].R6], "Multiple")
    twenty_words = " ".join(["caregiver"] * 20)
    nineteen_words = " ".join(["caregiver"] * 19)
    scored("R7", "Boundary-value test", "Twenty words and over 100 characters reaches the narrative eligibility boundary.",
           rows=[fixture_response("1", tfa_comments=twenty_words), fixture_response("2", tfa_comments=twenty_words)])
    scored("R7", "Boundary-value test", "Nineteen words remains too short despite over 100 characters.", expected=0,
           rows=[fixture_response("1", tfa_comments=nineteen_words), fixture_response("2", tfa_comments=nineteen_words)])
    scored("R7", "Conflicting-value test", "Different substantive narratives are not identical/copied just because both are long.", expected=0,
           rows=[fixture_response("1", tfa_comments=narrative), fixture_response("2", tfa_comments=" ".join(["Unrelated appointment transportation respite communication planning"] * 5))])
    unrelated_meta = fixture_metadata()
    unrelated_meta.loc[unrelated_meta.field_name.eq("tfa_comments"), "text_validation_type_or_show_slider_number"] = "email"
    scored("R7", "Study-specific test", "A field configured with structured email validation is excluded from narrative matching.", expected=0,
           rows=[fixture_response("1", tfa_comments=narrative), fixture_response("2", tfa_comments=narrative)], meta=unrelated_meta, pid=4931)
    count_meta = fixture_metadata()
    count_meta.loc[count_meta.field_name.eq("fif_num_autistic"), "select_choices_or_calculations"] = "0, 0 | 1, 1 | 2, 2 | 9, MORE THAN 3"
    scored("R8", "Capitalization test", "Uppercase count-bin label MORE THAN 3 retains its lower bound of four.",
           {"fif_num_autistic": "9", "fif_num_children": "1"}, meta=count_meta)
    count_meta = fixture_metadata()
    count_meta.loc[count_meta.field_name.eq("fif_num_autistic"), "select_choices_or_calculations"] = "0, 0 | 1, 1 | 2, 2 | 9, Más de 3"
    scored("R8", "Study-specific test", "Spanish count-bin meaning is decoded rather than treating option code nine as a count.",
           {"fif_num_autistic": "9", "fif_num_children": "8"}, 0, meta=count_meta, pid=5749)
    scored("R9", "Conflicting-value test", "Conflicting caregiver/child birth interval remains review-only and is not a fraud evidence family.",
           {"demo_momdob": "2000-01-01", "dob_child1": "2005"}, {"R9": 1, "Independent evidence families": 0},
           meta=pd.concat([fixture_metadata(), pd.DataFrame([_field("dob_child1", "text")])], ignore_index=True), columns=["R9", "Independent evidence families"])
    scored("R10", "Boundary-value test", "A subdomain is outside the exact configured disposable-domain set.",
           {"demo_email": "synthetic@sub.mailinator.com"}, 0)
    scored("R10", "Conflicting-value test", "A nonempty demographics email supplies the domain even when eligibility email differs.",
           {"demo_email": "synthetic@mailinator.com", "email_elig": "synthetic@example.invalid"})
    scored("R11", "Capitalization test", "Uppercase email still counts as an available participant contact.", {"demo_email": "SYNTHETIC@EXAMPLE.INVALID"}, 0)
    scored("R11", "Boundary-value test", "Completion code 1 is not the fully completed code 2, so R11 is not evaluable.",
           {"demo_email": "", "tfa_complete": "1"}, {"R11": 0, "R11 status": "Not evaluable"}, columns=["R11", "R11 status"])
    scored("R11", "Conflicting-value test", "A missing eligibility email does not override a supplied demographics email.", {"email_elig": ""}, 0)
    scored("R11", "Study-specific test", "When a study's demographics email is absent, a populated eligibility email prevents a missing-email flag.",
           {"demo_email": "", "email_elig": "synthetic@example.invalid"}, 0, pid=4581)
    scored("R12", "Positive test", "Start at 02:30 produces overnight context.", {"eligibility_timestamp": "2026-09-24 02:30:00"})
    scored("R12", "Negative test", "Start at midday is not overnight.", expected=0)
    scored("R12", "Whitespace test", "Outer whitespace is removed before parsing an overnight timestamp.", {"eligibility_timestamp": " 2026-09-24 02:30:00 "})
    scored("R12", "Conflicting-value test", "Malformed timestamp is not interpreted as an overnight start.", {"eligibility_timestamp": "not-a-date"}, 0)
    scored("R14", "Capitalization test", "Capitalized metadata pronoun and gender labels preserve their meanings.",
           {"demo_gender": "6"}, meta=scale_caps)
    scored("R14", "Conflicting-value test", "He and woman pairing is also context-only even when its nominal prompt points are nonzero.",
           {"pronouns_elig": "9"}, {"R14": 1, "Recommended action": "Pay now", "Independent evidence families": 0},
           columns=["R14", "Recommended action", "Independent evidence families"])
    no_meta = fixture_metadata()
    no_meta.loc[no_meta.field_name.eq("fif_child_needs"), ["field_type", "select_choices_or_calculations"]] = ["radio", "0, NO | 1, YES"]
    scored("R15", "Capitalization test", "Uppercase explicit No label is matched case-insensitively.", {"fif_child_needs": "0"}, meta=no_meta)
    scored("R15", "Conflicting-value test", "No-disability wording plus an impossible autistic-child count remains one count evidence family, not two.",
           {"fif_child_needs": "0", "fif_num_autistic": "2", "fif_num_children": "1"},
           {"R15": 1, "Logical evidence": 1, "Independent evidence families": 1}, columns=["R15", "Logical evidence", "Independent evidence families"])
    no_meta = fixture_metadata().loc[lambda frame: frame.field_name.ne("fif_child_needs")]
    scored("R15", "Study-specific test", "A study without a verified disability-response meaning cannot treat stored zero as an explicit No.",
           {"fif_child_needs": "0"}, {"R15": 0, "R15 status": "Not applicable"}, meta=no_meta, pid=4581, columns=["R15", "R15 status"])
    for rule, flag, rid in [("EX_ARCHIVE_5749", "archive_clone", 223), ("EX_PRELAUNCH_5749", "prelaunch_protocol", 460)]:
        add(rule, "Positive test", "An interior record number belongs to the documented PID 5749 exclusion interval.",
            {"triggered": True, "retained": False}, lambda flag=flag, rid=rid: _filter_flag(5749, rid, which=flag), 5749)
        add(rule, "Whitespace test", "Record-ID whitespace is trimmed before study partitioning.",
            {"triggered": True, "retained": False}, lambda flag=flag, rid=rid: _filter_flag(5749, f" {rid} ", which=flag), 5749)
        add(rule, "Non-trigger protection test", "Record 474 beyond the final exclusion boundary stays in scope when no keyword is present.",
            {"triggered": False, "retained": True}, lambda flag=flag: _filter_flag(5749, 474, which=flag), 5749)
        add(rule, "Conflicting-value test", "A simultaneous keyword exclusion retains the study-specific reason without double-counting the record.",
            {"triggered": True, "retained": False}, lambda flag=flag, rid=rid: _filter_flag(5749, rid, "test", which=flag), 5749)
    add("G_SOURCE", "Missing-value test", "Absent API project PID cannot verify project identity.", "Rejected with ValueError",
        lambda: _raises_value_error(lambda: sources.validate_project_identity({"project_title": "Fixture"}, {"project_id": 4797, "study_name": "Fixture"})))
    add("G_SOURCE", "Capitalization test", "Case-changed title is not silently accepted as the exact reference-study title.", "Rejected with ValueError",
        lambda: _raises_value_error(lambda: sources.validate_project_identity({"project_id": 4797, "project_title": "FIXTURE"}, {"project_id": 4797, "study_name": "Fixture"})))
    def valid_identity():
        sources.validate_project_identity({"project_id": " 4797 ", "project_title": "  Fixture   Study  "},
                                          {"project_id": 4797, "study_name": "Fixture Study"})
        return "Accepted"
    add("G_SOURCE", "Whitespace test", "Project-title whitespace normalization preserves exact identity words and PID.", "Accepted", valid_identity)
    add("G_SOURCE", "Non-trigger protection test", "A correct single-event project identity passes without changing its records.", "Accepted", valid_identity)
    scored("G_COMPLETE", "Boundary-value test", "Completion status 1 remains incomplete at the threshold below required status 2.",
           {"tfa_complete": "1"}, {"Recommended action": "Incomplete - not eligible"}, columns=["Recommended action"])
    scored("G_COMPLETE", "Non-trigger protection test", "Context-only overnight timing does not revoke a completed, otherwise clear response.",
           {"eligibility_timestamp": "2026-09-24 02:00:00"}, {"Recommended action": "Pay now"}, columns=["Recommended action"])
    scored("G_CONTACT", "Capitalization test", "Email confirmation comparison is case-insensitive.",
           {"demo_email_confirm": "SYNTHETIC1@EXAMPLE.INVALID"}, {"Payment contact hold": 0}, columns=["Payment contact hold"])
    scored("G_CONTACT", "Boundary-value test", "Multiple at signs fail the single-address syntax requirement.",
           {"demo_email": "synthetic@@example.invalid"}, {"Payment contact hold": 1}, columns=["Payment contact hold"])
    scored("G_CONTACT", "Non-trigger protection test", "A plus-address is syntactically valid and alone is not evidence of a disposable account.",
           {"demo_email": "synthetic+caregiver@example.invalid"}, {"Payment contact hold": 0}, columns=["Payment contact hold"])
    scored("G_INDEPENDENT", "Conflicting-value test", "A source-mapping problem prevents interpreting two risk families as a confident integrity recommendation.",
           {"get_time_tfa": "1", "elig_knee___7": "1", "source_mapping_issue": True},
           {"Independent evidence families": 2, "Recommended action": "Check by hand"}, columns=["Independent evidence families", "Recommended action"])
    return cases


def applicability_notes() -> dict[tuple[str, str], str]:
    """Specific reasons for unexecutable or non-meaningful rule/type combinations."""
    notes = {}
    for rule in [*screening.RULES, "EX_TEST_WORD", "EX_ARCHIVE_5749", "EX_PRELAUNCH_5749",
                 "G_SOURCE", "G_COMPLETE", "G_CONTACT", "G_INDEPENDENT"]:
        notes[(rule, "Previously misclassified record test")] = (
            "No independently established previously misclassified historical record was supplied for this legacy rule. "
            "Synthetic boundary cases are not represented as observed historical errors; prospective revised-policy replays are reported separately.")
    for rule in ("R1", "R2", "R3"):
        notes[(rule, "Capitalization test")] = "This predicate compares numeric durations; alphabetic capitalization has no semantic meaning. Missing, negative, boundary and whitespace numeric cases execute separately."
        notes[(rule, "Study-specific test")] = "The existing timing predicate has no PID branch and uses the same configured threshold in every study. Study-specific threshold suitability is a candidate research decision, not an implemented distinction."
    for rule in ("R6", "R12"):
        notes[(rule, "Capitalization test")] = "REDCap timestamps used here are numeric YYYY-MM-DD HH:MM:SS, with no alphabetic tokens to capitalize. Timestamp parsing and malformed-value tests execute separately."
    notes[("R9", "Capitalization test")] = "This rule compares numeric ages/birth dates and US ZIP digits. The country indicator is a numeric yes/no code; alphabetic identity labels do not enter this predicate."
    notes[("R10", "Study-specific test")] = "The documented exact temporary-domain list is shared across PIDs; no study-specific domain rule is implemented."
    notes[("R12", "Study-specific test")] = "The existing overnight context predicate uses each exported local start hour and has no PID-specific interval. It never blocks payment on its own."
    notes[("R13", "Boundary-value test")] = "The attention rule is categorical set membership, without an ordered numeric cutoff. Unanswered, one correct, one incorrect, multiple selected, inaccessible-image and absent-item cases execute separately."
    notes[("R14", "Boundary-value test")] = "Pronoun and gender categories have no numerical ordering or meaningful cutoff. Missing, approved context pairs and plausible non-trigger combinations execute separately."
    for rule in ("EX_ARCHIVE_5749", "EX_PRELAUNCH_5749"):
        notes[(rule, "Capitalization test")] = "Study-partition rules require positive-integer record IDs. Letters and letter case cannot be valid identifiers for these numeric intervals."
    notes[("G_SOURCE", "Boundary-value test")] = "Identity validation uses exact configured PID/title equality rather than an ordered numeric threshold. Wrong PID/title, absent PID, unsupported repeated structure and valid normalized identity execute separately."
    notes[("G_COMPLETE", "Capitalization test")] = "Completion status is a numeric REDCap code; capitalization does not alter numeric meaning. Status 0, 1, 2, blank and whitespace cases execute separately."
    notes[("G_COMPLETE", "Study-specific test")] = "The current completion gate requires the same four canonical sections for every included PID; no PID-specific completion exception is implemented."
    notes[("G_INDEPENDENT", "Capitalization test")] = "This combined gate consumes already calculated integer evidence-family counts and Boolean holds; it has no lexical input. Component normalization is independently tested."
    notes[("G_INDEPENDENT", "Whitespace test")] = "The gate receives generated integer counts, not respondent text. Whitespace normalization occurs in the independently tested component rules."
    notes[("G_INDEPENDENT", "Missing-value test")] = "Evidence-family counts are always generated from explicit Boolean component flags. Missing raw responses are tested at their component rule and do not create a missing internal family count."
    notes[("G_INDEPENDENT", "Study-specific test")] = "The legacy combination threshold has no PID branch; within-study duplication and source-specific rule applicability are tested separately. The revised wrapper changes refusal to human review."
    return notes


def complete_applicability_matrix(results: pd.DataFrame, *, notes: dict[tuple[str, str], str] | None = None,
                                  rule_ids: Iterable[str] | None = None) -> pd.DataFrame:
    """Document only justified N/A cells in the requested rule-by-test-type grid.

    Missing applicable tests remain missing and cause the coverage gate to fail.
    This helper never invents a passing execution for absent evidence.
    """
    result = results.copy()
    if "Test applicability" not in result:
        result["Test applicability"] = "Applicable"
    result["Test applicability"] = result["Test applicability"].fillna("Applicable")
    if "Applicability rationale" not in result:
        result["Applicability rationale"] = ""
    result["Applicability rationale"] = result["Applicability rationale"].fillna("")
    reasons = {**applicability_notes(), **(notes or {})}
    rows = []
    for rule in (set(rule_ids) if rule_ids is not None else set(result["Rule ID"])):
        present = set(result.loc[result["Rule ID"].eq(rule), "Test type"])
        for test_type in sorted(REQUIRED_TEST_TYPES - present):
            reason = reasons.get((rule, test_type))
            if not reason:
                continue
            unavailable = test_type == "Previously misclassified record test"
            rows.append({
                "Test ID": f"{rule}-NA-{test_type.lower().replace(' ', '-')}", "Rule ID": rule,
                "Test type": test_type, "Study PID": "Not applicable", "Study name": "Rule-level applicability assessment",
                "Input condition": reason, "Expected result": "Not applicable; no execution claimed",
                "Actual result": "Not available" if unavailable else "Not applicable",
                "Pass or fail": "Not applicable", "Failure explanation": "", "Code correction": "Not applicable",
                "Retest result": "Historical adjudicated replay unavailable" if unavailable else "Not applicable",
                "Evidence origin": "Historical evidence unavailable" if unavailable else "Documented test-applicability assessment",
                "Test applicability": "Unavailable historical evidence" if unavailable else "Not applicable",
                "Applicability rationale": reason,
            })
    if rows:
        result = pd.concat([result, pd.DataFrame(rows, columns=TEST_COLUMNS)], ignore_index=True)
    return result


def run_regression_tests(extra_cases: Iterable[RegressionCase] = (), *, rule_id_aliases: dict[str, str] | None = None) -> pd.DataFrame:
    """Execute tests; exceptions become explicit failures, never successful evidence.

    Extra cases should include independently assessed real-record replays when
    available. Synthetic legacy-defect examples must not be relabeled as observed
    previously misclassified respondents.
    """
    aliases = rule_id_aliases or {}
    records = []
    for case in [*build_regression_cases(), *extra_cases]:
        expected = _json(case.expected)
        try:
            actual = _json(case.execute())
            passed = _same_result(json.loads(expected), json.loads(actual))
            failure = "" if passed else "Actual production result differs from the independently specified expected result."
        except Exception as exc:
            # Exception messages may contain record data; only class is exported.
            actual = f"Execution raised {type(exc).__name__}"
            passed, failure = False, "Production operation raised an exception; inspect restricted execution logs."
        records.append({
            "Test ID": case.test_id, "Rule ID": aliases.get(case.rule_id, case.rule_id),
            "Test type": case.test_type, "Study PID": case.study_pid, "Study name": case.study_name,
            "Input condition": case.input_condition, "Expected result": expected, "Actual result": actual,
            "Pass or fail": "Pass" if passed else "Fail", "Failure explanation": failure,
            "Code correction": "Not needed" if passed else "Required before production use",
            "Retest result": "Not required; initial test passed" if passed else "Pending correction and retest",
            "Evidence origin": case.evidence_origin,
            "Test applicability": "Applicable", "Applicability rationale": "",
        })
    result = pd.DataFrame(records, columns=TEST_COLUMNS)
    if not result["Test ID"].is_unique:
        raise ValueError("Regression test IDs must be unique")
    alias_notes = {(aliases.get(rule, rule), kind): reason for (rule, kind), reason in applicability_notes().items()}
    return complete_applicability_matrix(result, notes=alias_notes)


def assert_regression_coverage(results: pd.DataFrame, implemented_rule_ids: Iterable[str], *, require_all_types=True) -> None:
    """Fail closed for absent/failed implemented-rule evidence and required types."""
    required = set(implemented_rule_ids)
    missing = required - set(results["Rule ID"])
    applicable = results["Pass or fail"].ne("Not applicable")
    failed = set(results.loc[applicable & results["Pass or fail"].ne("Pass"), "Rule ID"]) & required
    no_executions = required - set(results.loc[applicable, "Rule ID"])
    unjustified_na = results["Pass or fail"].eq("Not applicable") & results.get("Applicability rationale", pd.Series("", index=results.index)).fillna("").str.strip().eq("")
    absent_types = {rule: sorted(REQUIRED_TEST_TYPES - set(results.loc[results["Rule ID"].eq(rule), "Test type"]))
                    for rule in required} if require_all_types else {}
    absent_types = {rule: kinds for rule, kinds in absent_types.items() if kinds}
    if missing or failed or absent_types or no_executions or unjustified_na.any():
        raise ValueError(f"Regression gate failed: uncovered rules={sorted(missing)}; failed rules={sorted(failed)}; "
                         f"missing test types={absent_types}; rules without execution={sorted(no_executions)}; unjustified N/A={int(unjustified_na.sum())}")


def build_rule_validation_results(rule_specification: pd.DataFrame, rule_activity: pd.DataFrame,
                                  regression_results: pd.DataFrame) -> pd.DataFrame:
    """Table 8 from observed record/rule activity; no invented adjudication metrics.

    ``rule_activity`` has one row per actual response and rule and must include
    Rule ID, Study PID, Study name, Response key, Evaluated, Triggered. Boolean
    values are required (strings like 'False' are rejected). Each PID and an
    explicitly labeled all-study total are reported per rule. Trigger percentage
    uses evaluable records, never synthetic cases. Unknown accuracy counts remain
    Not available, not zeros; Unable to adjudicate counts triggered records whose
    correctness lacks independent record-level reference adjudication.
    """
    required = {"Rule ID", "Study PID", "Study name", "Response key", "Evaluated", "Triggered"}
    if not required <= set(rule_activity):
        raise ValueError(f"Rule activity is missing columns: {sorted(required - set(rule_activity))}")
    if rule_activity.duplicated(["Rule ID", "Response key"]).any():
        raise ValueError("Rule activity must contain at most one observed row per rule/response")
    if rule_activity[["Study PID", "Study name", "Response key"]].isna().any().any():
        raise ValueError("Rule activity requires study provenance and response keys")
    for name in ("Evaluated", "Triggered"):
        if not pd.api.types.is_bool_dtype(rule_activity[name]) or rule_activity[name].isna().any():
            raise ValueError(f"{name} must contain nonmissing Boolean values")
    if (rule_activity["Triggered"] & ~rule_activity["Evaluated"]).any():
        raise ValueError("A triggered rule must be evaluable")
    if rule_specification["Rule ID"].duplicated().any():
        raise ValueError("Rule specification IDs must be unique")
    unknown = set(rule_activity["Rule ID"]) - set(rule_specification["Rule ID"])
    if unknown:
        raise ValueError(f"Activity has unspecified rules: {sorted(unknown)}")
    studies = rule_activity[["Study PID", "Study name"]].drop_duplicates()
    if studies["Study PID"].duplicated().any():
        raise ValueError("A study PID maps to multiple study names")
    records = []
    for spec in rule_specification.to_dict("records"):
        rule_id = spec["Rule ID"]
        activity = rule_activity.loc[rule_activity["Rule ID"].eq(rule_id)]
        tests = regression_results.loc[regression_results["Rule ID"].eq(rule_id)]
        applicable_tests = tests.loc[tests["Pass or fail"].ne("Not applicable")]
        status = "Not tested" if applicable_tests.empty else ("Fail" if applicable_tests["Pass or fail"].ne("Pass").any()
            else f"Pass ({len(applicable_tests)} executed cases; {len(tests) - len(applicable_tests)} documented N/A)")
        cohorts = [(pid, name, activity.loc[activity["Study PID"].eq(pid)]) for pid, name in studies.itertuples(index=False, name=None)]
        cohorts.append(("All studies", "All studies total (unique project/record keys)", activity))
        for pid, name, cohort in cohorts:
            evaluated = int(cohort["Evaluated"].sum())
            triggered = int(cohort["Triggered"].sum())
            available = not cohort.empty
            records.append({
                "Rule ID": rule_id, "Rule name": spec.get("Rule name", rule_id), "Study PID": pid, "Study name": name,
                "Records evaluated": evaluated if available else "Not available",
                "Records triggered": triggered if available else "Not available",
                "Trigger percentage": 100 * triggered / evaluated if evaluated else "Not estimable",
                "Confirmed correct triggers": "Not available", "False positives": "Not available", "False negatives": "Not available",
                "Unable to adjudicate": triggered if available else "Not available",
                "Estimated precision": "Not estimable", "Estimated recall": "Not estimable", "Regression-test status": status,
                "Recommendation": ("Do not use failed rule in production" if status == "Fail" else
                    "Independent adjudicated reference labels are required to estimate accuracy; regression passing verifies implementation only"),
                "Denominator notes": ("Evaluable observed records for this rule; excludes missing/not-applicable cases. Synthetic regression fixtures excluded."
                    if available else "No observed rule evaluation supplied; candidate/unimplemented activity is not assumed zero."),
            })
    return pd.DataFrame(records, columns=PERFORMANCE_COLUMNS)
