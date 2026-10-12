"""Tests for Valid_Data and the section D attitude groups."""

import numpy as np
import pandas as pd
import pytest

import valid_data_clusters as vdc


def _verification(rows: list[tuple]) -> pd.DataFrame:
    """A minimal PID 6277 export, one tuple per verification record."""
    columns = ["source_pid", "source_record_id", "verify_birth_year", "original_birth_year", "verify_zip",
               "original_zip", "verify_education", "original_education", "passed_verification"]
    return pd.DataFrame(rows, columns=columns)


def _metadata(choices: dict[str, str] | None = None) -> pd.DataFrame:
    """REDCap metadata for every scored question: 1-5 radios unless overridden."""
    five = " | ".join(f"{code}, option {code}" for code in range(1, 6))
    six = " | ".join(f"{code}, option {code}" for code in range(1, 7))
    rows = {question: {"field_type": "radio", "select_choices_or_calculations": five,
                       "text_validation_min": "", "text_validation_max": ""}
            for questions, _ in vdc.SCORES.values() for question in questions}
    for question in ["tfa_scared", "tfa_sad"]:
        rows[question] = {"field_type": "slider", "select_choices_or_calculations": "not at all | | a lot",
                          "text_validation_min": "1", "text_validation_max": "5"}
    for question in ["tfa_scan_any", "tfa_scan_mine", "tfa_screen_accuracy"]:
        rows[question]["select_choices_or_calculations"] = six
    for question, value in (choices or {}).items():
        rows[question]["select_choices_or_calculations"] = value
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("field_name").reset_index()


def test_recheck_follows_the_redcap_formula_and_catches_stale_flags():
    verification = _verification([
        ("4581", "1", "1990", "1990", "29205", "29205", "7", "7", "1"),  # pass, stored correctly
        ("4581", "2", "1990", "1991", "29205", "29205", "7", "7", "0"),  # birth year differs: fail
        ("4581", "3", "1990", "1990", "02134", "2134", "7", "7", ""),    # pass that REDCap never stored
        ("4581", "4", "", "1990", "29205", "29205", "7", "7", ""),       # not answered yet
        ("4581", "5", "1990", "", "29205", "29205", "7", "7", ""),       # original answer missing
    ])
    checked = vdc.verification_results(verification).set_index("response_key")

    assert checked["verified"].tolist() == [True, False, True, False, False]
    assert checked.index[checked["stale_in_redcap"]].tolist() == ["4581-3"]
    assert checked.loc["4581-2", "verification"] == vdc.MISMATCH_LABEL
    assert checked.loc["4581-4", "verification"] == vdc.AWAITING_LABEL
    assert checked.loc["4581-5", "verification"] == vdc.MISSING_ORIGINAL_LABEL


def test_valid_data_is_cleared_plus_verified_finished_surveys():
    finished = {field: "2" for field in vdc.COMPLETION_FIELDS}
    responses = pd.DataFrame([
        {"response_key": "4797-1", **finished},
        {"response_key": "4581-2", **finished},
        {"response_key": "4581-3", **finished},
        {"response_key": "4581-4", **finished},
        {"response_key": "5749-5", **finished},
        {"response_key": "5749-6", **{**finished, "tfa_complete": "0"}},
        {"response_key": "4797-7", **finished},
    ])
    screen = pd.DataFrame({"response_key": ["4797-1", "4581-2", "4581-3", "4581-4"],
                           "Payment_Status": ["Pay Now", "Do Not Pay", "Manual Review", "Manual Review"]})
    verification = _verification([
        ("4581", "2", "1990", "1990", "29205", "29205", "7", "7", "1"),
        ("4581", "3", "1990", "1990", "29205", "29210", "7", "7", "0"),
        ("4581", "4", "", "1990", "", "29205", "", "7", ""),
        ("5749", "5", "1985", "1985", "29063", "29063", "8", "8", "1"),
        ("5749", "6", "1985", "1985", "29063", "29063", "8", "8", "1"),
    ])
    out = vdc.assign_validity(responses, screen, verification).set_index("response_key")

    assert out.index[out["valid"]].tolist() == ["4797-1", "4581-2", "5749-5"]
    assert out.loc["4581-3", "route"] == "Out: verification answers did not match"
    assert out.loc["4581-4", "route"] == "Waiting: verification not answered yet"
    assert out.loc["5749-6", "route"] == "Out: survey never finished"  # verified, but nothing complete to analyse
    assert out.loc["4797-7", "route"] == "Out: flagged, never sent verification"
    assert out.loc["5749-5", "became_valid"] == "Verified, was Excluded"


def test_verification_for_an_unknown_response_stops_the_run():
    responses = pd.DataFrame([{"response_key": "4581-1", **{field: "2" for field in vdc.COMPLETION_FIELDS}}])
    screen = pd.DataFrame({"response_key": ["4581-1"], "Payment_Status": ["Manual Review"]})
    verification = _verification([("4581", "999", "1990", "1990", "29205", "29205", "7", "7", "1")])
    with pytest.raises(ValueError, match="missing from the studies"):
        vdc.assign_validity(responses, screen, verification)


def test_scores_use_live_ranges_and_fix_the_two_scale_errors():
    ranges = vdc.question_ranges({4797: _metadata(), 4581: _metadata()}).set_index("question")
    assert tuple(ranges.loc["tfa_scan_any", ["low", "high"]]) == (1, 6)
    assert tuple(ranges.loc["tfa_screen_accuracy", ["low", "high"]]) == (1, 5)  # 6 = would not do the test

    answers = pd.DataFrame({question: ["", ""] for question in ranges.index})  # REDCap exports blanks as ""
    answers.loc[0, ["tfa_scared", "tfa_sad", "tfa_scan_any", "tfa_scan_mine", "tfa_screen_accuracy"]] = \
        ["2", "1", "6", "6", "6"]
    answers.loc[1, "tfa_screen_accuracy"] = "5"
    scores = vdc.attitude_scores(answers, ranges.reset_index())

    assert scores.loc[0, "Not scared or sad"] == 87.5  # the worked example in section D.2
    assert scores.loc[0, "Feels screening is right"] == 0  # never below zero
    assert np.isnan(scores.loc[0, "OK with a less accurate test"])
    assert scores.loc[1, "OK with a less accurate test"] == 100


def test_answer_ranges_must_agree_across_studies():
    five = " | ".join(f"{code}, option {code}" for code in range(1, 6))
    with pytest.raises(ValueError, match="tfa_scan_any"):
        vdc.question_ranges({4797: _metadata(), 4581: _metadata({"tfa_scan_any": five})})


def test_the_more_accepting_group_is_named_higher_acceptability():
    rng = np.random.default_rng(0)
    scores = pd.DataFrame(np.vstack([rng.normal(40, 5, (40, len(vdc.SCORES))),
                                     rng.normal(80, 5, (40, len(vdc.SCORES)))]), columns=list(vdc.SCORES))
    fit = vdc.fit_groups(scores, k=2, seed=1, n_init=10)

    assert fit.labels.iloc[:40].eq(vdc.CONDITIONAL).all()
    assert fit.labels.iloc[40:].eq(vdc.HIGHER).all()
    assert fit.assign(scores).equals(fit.labels)


def test_group_comparison_uses_the_documented_formula():
    groups = pd.Series([vdc.HIGHER] * 250 + [vdc.CONDITIONAL] * 350)
    definitely_yes = pd.Series([1.0] * 175 + [0.0] * 75 + [1.0] * 158 + [0.0] * 192)
    row = vdc.compare_groups({"Definitely yes": definitely_yes}, groups).iloc[0]

    p_higher, p_conditional = 175 / 250, 158 / 350
    margin = 1.96 * np.sqrt(p_higher * (1 - p_higher) / 250 + p_conditional * (1 - p_conditional) / 350)
    assert row["Difference"] == pytest.approx((p_higher - p_conditional) * 100)
    assert row["95% range low"] == pytest.approx((p_higher - p_conditional - margin) * 100)
    assert row["Clear difference?"] == "Yes"
