"""Synthetic boundary and policy checks, independent of credentials/live records."""
from types import SimpleNamespace

import pandas as pd
import pytest

import refined_screening as rs


def field(name, kind="radio", choices="", form="eligibility", **extra):
    return {"field_name": name, "field_type": kind, "form_name": form,
            "select_choices_or_calculations": choices, "branching_logic": "", **extra}


def metadata(attention=True):
    fields = [
        field("pronouns_elig", choices="9, He/Him/His | 8, She/Her/Hers | 7, They/Them/Theirs"),
        field("demo_gender", choices="6, Man | 5, Woman | 4, Non-binary", form="demographics"),
        field("fif_child_needs", "yesno", form="family_information_form"),
        field("fif_num_autistic", choices="0, 0 | 1, 1 | 2, 2 | 9, More than 3", form="family_information_form"),
        field("fif_num_children", choices="1, 1 | 2, 2 | 8, 8+", form="family_information_form"),
        field("age_confirm_elig", "text"),
        field("demo_country", "yesno", choices="1, US\n2, Canada\n3, Other", form="demographics"),
        field("tfa_comments", "notes", form="tfa"),
    ]
    if attention:
        fields.append(field("elig_knee", "checkbox", "3, Puts on a Band-Aid | 7, Flips on a light | 8, There is no image | 9, I prefer not to answer"))
    return pd.DataFrame(fields)


def response(record_id="1", **overrides):
    data = {"record_id": record_id, **{f: "2" for f in rs.COMPLETION_FIELDS},
            "get_time_fif": "4", "get_time_val": "1", "get_time_tfa": "10", "get_time_demo": "2",
            "demo_email": f"participant{record_id}@example.org", "eligibility_timestamp": "2026-09-24 12:00:00",
            "pronouns_elig": "8", "demo_gender": "5", "fif_child_needs": "1",
            "fif_num_autistic": "1", "fif_num_children": "2", "age_confirm_elig": "35",
            "demo_country": "1", "zip_demo": "29201", "elig_knee___3": "1",
            "elig_knee___7": "0", "elig_knee___8": "0", "elig_knee___9": "0", "tfa_comments": ""}
    data.update(overrides)
    return data


def score(rows, meta=None, pid=4797, verified=True):
    return rs.score_study(pd.DataFrame(rows), f"Synthetic study (PID {pid})",
                          metadata() if meta is None else meta, project_id=pid,
                          provenance_verified=verified)


def test_correct_attention_answer_uses_label_meaning_not_assumed_code_two():
    row = score([response()]).iloc[0]
    assert row.R13 == 0
    assert row["Recommended action"] == "Pay now"


@pytest.mark.parametrize("options,flag,strong,action", [
    ({"elig_knee___3": "0", "elig_knee___7": "1"}, 1, 1, "Check by hand"),
    ({"elig_knee___7": "1"}, 1, 1, "Check by hand"),
    ({"elig_knee___3": "0", "elig_knee___8": "1"}, 1, 0, "Check by hand"),
    ({"elig_knee___3": "0", "elig_knee___9": "1"}, 1, 0, "Check by hand"),
    ({"elig_knee___3": "0"}, 0, 0, "Check by hand"),
])
def test_attention_distinguishes_failure_accessibility_and_missing(options, flag, strong, action):
    row = score([response(**options)]).iloc[0]
    assert row.R13 == flag
    assert row["Attention evidence"] == strong
    assert row["Recommended action"] == action


def test_unasked_attention_is_not_a_failure_or_missing_answer_hold():
    row = score([response()], metadata(attention=False), pid=4581).iloc[0]
    assert row["R13 status"] == "Not applicable"
    assert row["Recommended action"] == "Pay now"


def test_changed_attention_meaning_stops_scoring():
    meta = metadata()
    meta.loc[meta.field_name.eq("elig_knee"), "select_choices_or_calculations"] = "1, Different question"
    with pytest.raises(ValueError, match="Band-Aid choice meaning"):
        score([response()], meta)


@pytest.mark.parametrize("pronouns,gender,flag", [("8", "6", 1), ("9", "5", 1), ("7", "6", 0), ("", "6", 0)])
def test_identity_pairing_never_blocks_payment(pronouns, gender, flag):
    row = score([response(pronouns_elig=pronouns, demo_gender=gender)]).iloc[0]
    assert row.R14 == flag
    assert row["Recommended action"] == "Pay now"
    assert row["Independent evidence families"] == 0


@pytest.mark.parametrize("needs,autistic,flag", [("0", "1", 1), ("0", "9", 1), ("", "1", 0), ("0", "0", 0)])
def test_disability_rule_requires_explicit_no_and_decoded_positive_count(needs, autistic, flag):
    row = score([response(fif_child_needs=needs, fif_num_autistic=autistic, fif_num_children="8")]).iloc[0]
    assert row.R15 == flag
    assert row["Independent evidence families"] == 0
    assert row["Recommended action"] == ("Check by hand" if flag else "Pay now")


def test_overlapping_timing_rules_cannot_manufacture_independent_corroboration():
    row = score([response(get_time_fif="1", get_time_val="0.1", get_time_tfa="1", get_time_demo="0.1")]).iloc[0]
    assert (row.R1, row.R2, row.R3) == (1, 1, 1)
    assert row["Independent evidence families"] == 1
    assert row["Risk score"] == 5
    assert row["Score-only proposed action"] == "Do not pay"
    assert row["Recommended action"] == "Check by hand"


def test_independent_timing_and_attention_concerns_support_refusal_but_source_error_overrides():
    data = response(get_time_tfa="1", elig_knee___3="0", elig_knee___7="1")
    assert score([data]).iloc[0]["Recommended action"] == "Do not pay"
    data["source_mapping_issue"] = True
    row = score([data]).iloc[0]
    assert row["Recommended action"] == "Check by hand"
    assert row["Review priority"] == "Resolve source mapping first"


def test_incomplete_response_outranks_flags_and_is_not_paid():
    row = score([response(tfa_complete="0", get_time_tfa="1", elig_knee___7="1")]).iloc[0]
    assert row["Recommended action"] == "Incomplete - not eligible"


@pytest.mark.parametrize("tfa,flag", [("7.85", 0), ("7.849999", 1), ("", 0), ("-1", 0)])
def test_timing_floor_is_strict_and_missing_negative_values_are_not_fast(tfa, flag):
    row = score([response(get_time_tfa=tfa)]).iloc[0]
    assert row.R2 == flag
    if tfa in {"", "-1"}:
        assert row["R2 status"] == "Not evaluable"


def test_burst_counts_other_records_and_has_inclusive_two_minute_boundary():
    stamps = pd.Series(pd.to_datetime(["2026-09-24 12:00:00", "2026-09-24 12:02:00", "2026-09-24 12:02:01", None]))
    assert rs._arrival_counts(stamps).tolist() == [1, 2, 1, 0]


def test_recruitment_burst_does_not_block_otherwise_clear_responses():
    rows = score([response(str(i)) for i in range(3)])
    assert rows.R6.eq(1).all()
    assert rows["Recommended action"].eq("Pay now").all()


def test_substantive_matching_text_with_timing_is_independent_but_short_comments_are_not():
    text = "Our family carefully considered the practical benefits and challenges of early screening because accessible support services and clear communication would help us make informed decisions together."
    rows = score([response("1", tfa_comments=text, get_time_tfa="1"), response("2", tfa_comments=text)])
    assert rows.R7.eq(1).all()
    assert rows.iloc[0]["Recommended action"] == "Do not pay"
    assert rows.iloc[1]["Recommended action"] == "Check by hand"
    short = score([response("1", tfa_comments="No concerns"), response("2", tfa_comments="No concerns")])
    assert short.R7.eq(0).all()


def test_open_ended_child_count_is_not_misread_as_exactly_eight():
    row = score([response(fif_num_autistic="9", fif_num_children="8")]).iloc[0]
    assert row.R8 == 0
    assert row["Children count is open ended"] == 1
    impossible = score([response(fif_num_autistic="9", fif_num_children="1")]).iloc[0]
    assert impossible.R8 == 1
    assert impossible["Logical evidence"] == 1


def test_branching_audit_ignores_checkbox_zeros_and_valid_alternative_or_paths():
    meta = pd.concat([metadata(), pd.DataFrame([
        field("follow", "checkbox", "1, Yes | 2, No", form="family_information_form",
              branching_logic='[fif_num_autistic] > 2'),
        field("alternative", "notes", form="family_information_form",
              branching_logic='[fif_num_autistic] > 0 or [pregnant] = 1'),
    ])], ignore_index=True)
    data = response(fif_num_autistic="0", follow___1="0", follow___2="0", alternative="Legitimately visible")
    assert score([data], meta).iloc[0].R8 == 0
    data["follow___1"] = "1"
    row = score([data], meta).iloc[0]
    assert row.R8 == 1
    assert row["Logical evidence"] == 0  # Stored hidden answers can be stale.


def test_age_uses_response_date_not_today_or_over_eighteen_yesno_code():
    meta = metadata().loc[lambda d: d.field_name.ne("age_confirm_elig")]
    data = response(age_elig="1", demo_momdob="2009-09-25")
    row = score([data], meta).iloc[0]
    assert row["Reported caregiver age"] == 16
    assert row.R9 == 1
    assert row["Caregiver age source"] == "Date of birth and survey start date"


def test_foreign_country_yesno_zero_is_not_us_despite_stale_choice_labels():
    row = score([response(demo_country="0", zip_demo="SW1A 1AA")]).iloc[0]
    assert row.R9 == 0


def test_shared_contact_is_held_across_studies_without_merging_same_record_ids():
    sources = SimpleNamespace(
        records_by_pid={4797: pd.DataFrame([response()]), 4931: pd.DataFrame([response()])},
        metadata_by_pid={4797: metadata(), 4931: metadata()},
        registry={pid: {"display_label": f"Study {pid}", "study_name": f"Study {pid}", "population": "Synthetic"}
                  for pid in [4797, 4931]})
    rows = rs.score_refined_records(sources)
    assert len(rows) == 2 and rows["Response key"].is_unique
    assert rows["Responses sharing payment email"].eq(2).all()
    assert rows["Recommended action"].eq("Check by hand").all()


def test_unverified_source_cannot_clear_payment():
    assert score([response()], verified=False).iloc[0]["Recommended action"] == "Check by hand"


def test_empty_screened_cohort_preserves_output_schema():
    result = rs.score_study(pd.DataFrame({"record_id": []}), "Empty", pd.DataFrame(columns=["field_name"]),
                           project_id=5749, provenance_verified=True)
    assert result.empty and {"Study", "REDCap PID", "Record ID", "Recommended action", "Risk score"} <= set(result)


def test_rating_selection_excludes_categorical_knowledge_codes_and_fingerprints_require_coverage():
    scales = [field(f"rating_{i}", choices="1, Not at all like me | 2, Somewhat like me | 3, Like me | 4, Very much like me",
                    form="values") for i in range(20)]
    scales.append(field("knowledge", choices="1, 1% | 2, 3% | 3, 10% | 4, 25%", form="tfa"))
    meta = pd.concat([metadata(), pd.DataFrame(scales)], ignore_index=True)
    vector = {f"rating_{i}": str(i % 4 + 1) for i in range(20)}
    rows = score([response("1", **vector, knowledge="1"), response("2", **vector, knowledge="4")], meta)
    assert rows["Rating items available"].eq(20).all()
    assert rows.R5.eq(1).all()  # Knowledge answers do not change a Likert fingerprint.
    assert rows["Records sharing response fingerprint"].eq(2).all()
    partial = {**vector, **{f"rating_{i}": "" for i in range(5)}}
    rows = score([response("1", **partial), response("2", **partial)], meta)
    assert rows.R5.eq(0).all()
    assert rows["R5 status"].eq("Not evaluable").all()
