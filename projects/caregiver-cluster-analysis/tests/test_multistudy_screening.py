"""The screening extended to every configured study.

These tests protect three things that are easy to break by accident:

* the bilingual study is counted once, not twice;
* extending the rules to four studies did not change the answers the published
  pipeline already gave for the two it covered;
* the workbook keeps the column layout and the four labels the September
  meeting agreed on, including which letter each one lands on.
"""

import os
import re
from pathlib import Path

import pytest

import bot_analysis as ba

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_DIR.parents[1]
OUTPUT_DIR = PROJECT_DIR / "Caregiver Outputs"
CACHE_DIR = PROJECT_DIR / "data_cache"

# Like the other integration tests here, these read the REDCap cache and need a
# credentialed .env to refresh it.  Skipping is honest; failing is not.
pytestmark = pytest.mark.skipif(
    not os.environ.get("REDCAP_API_URL") and not (REPOSITORY_ROOT / ".env").exists(),
    reason="needs REDCap credentials: set REDCAP_API_URL or add a repository .env",
)


@pytest.fixture(scope="module")
def flags():
    return ba.compute_screening_flags(PROJECT_DIR, CACHE_DIR)


@pytest.fixture(scope="module")
def planned():
    risk_table = ba.build_risk_table(OUTPUT_DIR, CACHE_DIR, PROJECT_DIR)
    return ba.build_final_review_plan(ba.build_review_triage(risk_table))


# ── Reach ───────────────────────────────────────────────────────────────────

def test_every_configured_study_is_screened(flags) -> None:
    configured = set(ba.build_study_registry(PROJECT_DIR))
    assert set(flags["source_project"]) == configured
    assert len(configured) >= 4


def test_response_key_is_unique_across_studies(planned) -> None:
    records = ba.build_records_sheet(planned)
    assert records["Response key"].is_unique


# ── The bilingual study is counted once ─────────────────────────────────────

def test_spanish_twins_are_folded_onto_their_english_base() -> None:
    combined = ba.load_combined_records(CACHE_DIR, PROJECT_DIR)
    leftovers = [
        column
        for column in combined.columns
        if ba.canonical_field_name(column) in set(combined.columns)
    ]
    assert leftovers == [], f"Spanish twins survived folding: {leftovers[:5]}"


def test_folding_keeps_every_spanish_answer() -> None:
    """A Spanish answer must survive the fold, not be dropped or duplicated."""
    field_map = ba.build_bilingual_field_map(PROJECT_DIR, CACHE_DIR)
    if field_map.empty:
        pytest.skip("no bilingual study in scope")

    assert (field_map["Folded onto"] != field_map["Spanish variable"]).all()

    registry = ba.build_study_registry(PROJECT_DIR)
    combined = ba.load_combined_records(CACHE_DIR, PROJECT_DIR)
    for project_id in field_map["REDCap PID"].unique():
        key = next(
            name for name, study in registry.items()
            if int(study["project_id"]) == int(project_id)
        )
        raw = ba._load_raw_project_records(CACHE_DIR, int(project_id))
        folded = combined[combined["source_project"].eq(key)].reset_index(drop=True)
        rows = field_map[field_map["REDCap PID"].eq(project_id)]
        text = lambda frame, column: (  # noqa: E731
            frame[column].astype("string").fillna("").str.strip()
        )
        for spanish, english in zip(rows["Spanish variable"], rows["Folded onto"]):
            if spanish not in raw.columns or english not in folded.columns:
                continue
            if english in raw.columns:
                # A twin pair: the Spanish answer fills in where English is blank.
                answered_in_spanish = text(raw, spanish).ne("") & text(raw, english).eq("")
            else:
                # Spanish only, renamed rather than merged: it must carry across whole.
                answered_in_spanish = text(raw, spanish).ne("")
            if not answered_in_spanish.any():
                continue
            rows_kept = answered_in_spanish.to_numpy()
            assert (
                text(folded, english)[rows_kept].to_numpy()
                == text(raw, spanish)[rows_kept].to_numpy()
            ).all(), f"{spanish} answers did not survive folding onto {english}"


def test_language_gate_is_stripped_before_branching_rules_are_compared() -> None:
    gated = "([fif_pregnant] <> \"\" and [fif_pregnant]=1) and [english_spanish] = '2'"
    plain = '([fif_pregnant] <> "" and [fif_pregnant]=1)'
    assert ba._strip_language_gate(gated) == plain
    assert ba._strip_language_gate("[english_spanish] = '2'") == ""


def test_age_alias_never_resolves_to_the_over_eighteen_question() -> None:
    # age_elig reads "Are you over the age of 18?" and is answered 1 or 2.
    # Treating it as an age in years would flag every single respondent.
    assert "age_elig" not in ba.FIELD_ALIASES["caregiver_age"]


# ── Nothing the published pipeline said has changed ─────────────────────────

def test_rules_reproduce_the_published_pipeline(flags) -> None:
    check = ba.verify_flags_against_pipeline(flags, OUTPUT_DIR)
    unexplained = check[check["Match"].eq("No")]
    assert unexplained.empty, f"unexplained rule drift: {list(unexplained['Rule'])}"


def test_no_response_the_pipeline_flagged_became_unflagged(flags) -> None:
    published = ba.load_record_flags(OUTPUT_DIR).copy()
    published["record_id"] = published["record_id"].astype(str)
    merged = published.merge(
        flags, on=["source_project", "record_id"], suffixes=("_published", "_now")
    )
    for rule in ba.RULE_CODES_R:
        lost = merged[f"{rule}_published"].astype(bool) & ~merged[f"{rule}_now"].astype(bool)
        assert not lost.any(), f"{rule} stopped firing on {int(lost.sum())} responses"


# ── The workbook contract from the September meeting ────────────────────────

def test_column_l_holds_exactly_the_four_agreed_labels(planned) -> None:
    labels = set(planned["Final review plan"])
    assert labels <= set(ba.FINAL_PLAN_ORDER)
    assert len(ba.FINAL_PLAN_ORDER) == 4


def test_no_sampling_instruction_survives_in_the_final_plan(planned) -> None:
    sampling = re.compile(r"random|sample|release|slower", re.IGNORECASE)
    assert not planned["Final review plan"].str.contains(sampling).any()


def test_redcap_pid_sits_between_study_and_record_id() -> None:
    assert ba.RECORDS_SHEET_COLUMNS[0] == "Study"
    assert ba.RECORDS_SHEET_COLUMNS[1] == "REDCap PID"
    assert ba.RECORDS_SHEET_COLUMNS[2] == "Record ID"


def test_column_k_is_the_score_and_column_l_is_the_decision() -> None:
    # The meeting refers to these two by letter, so the letters are part of the
    # contract: K is what the score alone would do, L is what actually happens.
    assert ba.RECORDS_SHEET_COLUMNS[10] == "Score-based category"
    assert ba.RECORDS_SHEET_COLUMNS[11] == "Final review plan"
    assert ba.RECORDS_SHEET_COLUMNS[12] == "Include in Analysis"
    # Anything added later goes to the right of M so those letters keep meaning.
    assert ba.RECORDS_SHEET_COLUMNS.index("Survey finished") > 12
    assert ba.RECORDS_SHEET_COLUMNS.index("Sections timed") > 12


def test_every_burst_response_carries_the_burst_tag(planned) -> None:
    tagged = planned["Arrived in a burst (R6)"].eq("Yes")
    assert (tagged == planned["check_burst_arrival"].astype(bool)).all()


def test_an_individually_evidenced_refusal_outranks_the_burst(planned) -> None:
    """The decision taken in September, locked in.

    R6 covers 94% of the online recruitment study, so it cannot rank one
    response in that study against another. A response with several serious
    rules against it keeps the label that says so.
    """
    assert ba.BURST_OUTRANKS_REJECTION is False
    both = (
        planned["Serious checks broken"].ge(ba.SERIOUS_RULES_FOR_REJECTION)
        & planned["check_burst_arrival"].astype(bool)
    )
    assert both.any(), "no overlap to test; the fixture has changed shape"
    assert planned.loc[both, "Final review plan"].eq(ba.FINAL_REJECT).all()


def test_the_burst_label_still_wins_over_a_hand_read(planned) -> None:
    """What "alone or with others" was actually written to fix.

    R4 + R6 is two mild rules. It is never a refusal, and it must come out as
    the burst label rather than as a timing-based release or a hand read.
    """
    mild_burst = (
        planned["check_burst_arrival"].astype(bool)
        & planned["Serious checks broken"].lt(ba.SERIOUS_RULES_FOR_REJECTION)
    )
    assert planned.loc[mild_burst, "Final review plan"].eq(ba.FINAL_BURST).all()


def test_unfinished_surveys_are_flagged_rather_than_paid_quietly(planned) -> None:
    """Whether an abandoned survey earns a gift card is nobody's rule to decide."""
    payments = ba.build_payment_list_sheet(planned)
    unfinished = payments["Survey finished"].eq("No")
    if not unfinished.any():
        pytest.skip("every cleared response finished the survey")
    assert payments.loc[unfinished, "Needs a decision before paying"].ne("").all()
    # Marked rows sort to the top, so nobody has to go looking for them.
    assert payments["Needs a decision before paying"].ne("").to_numpy()[: int(unfinished.sum())].all()


def test_finished_but_untimed_is_not_confused_with_unfinished(planned) -> None:
    """Migrated responses kept their answers and lost their timestamps."""
    summary = ba.build_unfinished_survey_summary(planned)
    totals = summary.loc[summary["Study"].eq("All studies")].iloc[0]
    assert totals["Cleared for payment"] == totals["Finished the survey"] + totals["Never finished it"]
    # A migrated response is finished, so it must never be counted as unfinished.
    migrated = planned["Survey finished"].eq("Yes") & planned["Sections timed"].eq(0)
    assert planned.loc[migrated, "Survey finished"].eq("Yes").all()


def test_include_in_analysis_is_one_exactly_for_pay_now(planned) -> None:
    included = planned["Include in Analysis"].eq(1)
    assert (included == planned["Final review plan"].eq(ba.FINAL_PAY)).all()
    assert set(planned["Include in Analysis"].unique()) <= {0, 1}


def test_a_serious_rule_is_never_released_for_payment(planned) -> None:
    # Pace can excuse a mild rule. It cannot excuse a contradiction.
    released = planned["Final review plan"].eq(ba.FINAL_PAY)
    assert not (released & planned["Serious checks broken"].ge(1)).any()


def test_same_rules_and_same_reason_always_give_the_same_label(planned) -> None:
    signature = (
        planned[[ba.CODE_TO_KEY[code] for code in ba.CODE_ORDER]]
        .astype(int).astype(str).agg("".join, axis=1)
    )
    labels = (
        planned.assign(_signature=signature)
        .groupby(["_signature", "Why this final plan"])["Final review plan"]
        .nunique()
    )
    assert labels.max() == 1


def test_data_dictionary_covers_every_study() -> None:
    dictionary = ba.build_study_data_dictionary(PROJECT_DIR, CACHE_DIR)
    registry = ba.build_study_registry(PROJECT_DIR)
    assert set(dictionary["REDCap PID"]) == {int(s["project_id"]) for s in registry.values()}
    for column in ("Project name", "First survey date", "Last survey date", "Notes", "Team notes"):
        assert column in dictionary.columns


def test_only_the_bilingual_study_is_described_as_bilingual() -> None:
    dictionary = ba.build_study_data_dictionary(PROJECT_DIR, CACHE_DIR)
    described = set(dictionary.loc[dictionary["Notes"].str.contains("Bilingual"), "REDCap PID"])
    carries_switch = set()
    for study in ba.build_study_registry(PROJECT_DIR).values():
        fields = set(ba._load_project_metadata(CACHE_DIR, int(study["project_id"]))["field_name"])
        if "english_spanish" in fields:
            carries_switch.add(int(study["project_id"]))
    assert described == carries_switch
