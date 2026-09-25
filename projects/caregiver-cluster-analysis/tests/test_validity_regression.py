"""Regression reporting cannot turn fixture counts into empirical accuracy."""
import pandas as pd
import pytest

import validity_regression as vr


@pytest.fixture(scope="module")
def executed():
    return vr.run_regression_tests()


def test_independent_production_fixtures_all_pass_with_honest_origin(executed):
    assert executed["Test ID"].is_unique
    tests = executed.loc[executed["Test applicability"].eq("Applicable")]
    assert tests["Pass or fail"].eq("Pass").all(), tests.loc[tests["Pass or fail"].ne("Pass")].to_dict("records")
    assert tests["Evidence origin"].str.contains("Synthetic regression fixture").all()
    vr.assert_regression_coverage(executed, [*vr.screening.RULES, "EX_TEST_WORD", "EX_ARCHIVE_5749",
        "EX_PRELAUNCH_5749", "G_SOURCE", "G_COMPLETE", "G_CONTACT", "G_INDEPENDENT"])
    # A synthetic legacy boundary cannot pretend to be a previously misclassified actual respondent.
    assert "Previously misclassified record test" not in set(tests["Test type"])
    assert executed.loc[executed["Pass or fail"].eq("Not applicable"), "Applicability rationale"].str.len().gt(20).all()


def test_rule_by_type_coverage_requires_explicit_evidence_or_justified_na(executed):
    truncated = executed.loc[~(executed["Rule ID"].eq("R1") & executed["Test type"].eq("Whitespace test"))]
    # Many other rules' whitespace tests do not prove R1's normalization works.
    with pytest.raises(ValueError, match="Whitespace test"):
        vr.assert_regression_coverage(truncated, executed["Rule ID"].unique())
    bad = executed.copy()
    bad.loc[bad["Pass or fail"].eq("Not applicable"), "Applicability rationale"] = ""
    with pytest.raises(ValueError, match="unjustified N/A"):
        vr.assert_regression_coverage(bad, executed["Rule ID"].unique())


def test_gate_rejects_uncovered_or_failed_rule(executed):
    with pytest.raises(ValueError, match="uncovered rules"):
        vr.assert_regression_coverage(executed, ["UNTESTED_RULE"], require_all_types=False)
    bad = executed.copy()
    bad.loc[bad["Rule ID"].eq("R1"), "Pass or fail"] = "Fail"
    with pytest.raises(ValueError, match=r"failed rules=\['R1'\]"):
        vr.assert_regression_coverage(bad, ["R1"], require_all_types=False)


def test_numeric_storage_equivalence_does_not_equate_booleans_and_counters():
    assert vr._same_result({"age": 16}, {"age": 16.0})
    assert not vr._same_result({"flag": True}, {"flag": 1})
    assert not vr._same_result({"flag": 0}, {"flag": 1})


def test_regression_exception_is_failure_and_error_message_is_not_exported(monkeypatch):
    def fail():
        raise ValueError("sensitive-response@example.invalid")
    monkeypatch.setattr(vr, "build_regression_cases", lambda: [])
    result = vr.run_regression_tests([vr.RegressionCase("bad", "R1", "Positive test", "Exception path", 1, fail)])
    assert result.iloc[0]["Pass or fail"] == "Fail"
    assert result.iloc[0]["Retest result"] == "Pending correction and retest"
    assert "sensitive-response" not in result.to_json()


@pytest.fixture
def observed_activity():
    # Small synthetic data used only to verify aggregation machinery; never an exported survey estimate.
    return pd.DataFrame([
        {"Rule ID": "R1", "Study PID": 4797, "Study name": "Fixture A", "Response key": "4797:1", "Evaluated": True, "Triggered": True},
        {"Rule ID": "R1", "Study PID": 4797, "Study name": "Fixture A", "Response key": "4797:2", "Evaluated": True, "Triggered": False},
        {"Rule ID": "R1", "Study PID": 4797, "Study name": "Fixture A", "Response key": "4797:3", "Evaluated": False, "Triggered": False},
        {"Rule ID": "R1", "Study PID": 4931, "Study name": "Fixture B", "Response key": "4931:1", "Evaluated": True, "Triggered": True},
    ])


def test_performance_denominators_use_evaluable_observations_not_all_rows_or_fixture_cases(observed_activity, executed):
    specification = pd.DataFrame([{"Rule ID": "R1", "Rule name": "Survey time"}, {"Rule ID": "PENDING", "Rule name": "Candidate"}])
    result = vr.build_rule_validation_results(specification, observed_activity, executed)
    study = result.loc[result["Rule ID"].eq("R1") & result["Study PID"].eq(4797)].iloc[0]
    assert study["Records evaluated"] == 2
    assert study["Records triggered"] == 1
    assert study["Trigger percentage"] == 50.0
    assert study["Unable to adjudicate"] == 1
    total = result.loc[result["Rule ID"].eq("R1") & result["Study PID"].eq("All studies")].iloc[0]
    assert total["Records evaluated"] == 3
    assert total["Records triggered"] == 2
    assert total["Trigger percentage"] == pytest.approx(100 * 2 / 3)
    assert result["Estimated precision"].eq("Not estimable").all()
    assert result["Estimated recall"].eq("Not estimable").all()
    for col in ["Confirmed correct triggers", "False positives", "False negatives"]:
        assert result[col].eq("Not available").all()
    candidate = result.loc[result["Rule ID"].eq("PENDING")]
    assert candidate["Records evaluated"].eq("Not available").all()
    assert candidate["Records triggered"].eq("Not available").all()
    assert candidate["Regression-test status"].eq("Not tested").all()


def test_performance_rejects_duplicate_grain_and_trigger_without_evaluation(observed_activity, executed):
    specification = pd.DataFrame([{"Rule ID": "R1", "Rule name": "Survey time"}])
    with pytest.raises(ValueError, match="at most one"):
        vr.build_rule_validation_results(specification, pd.concat([observed_activity, observed_activity.iloc[:1]]), executed)
    bad = observed_activity.copy()
    bad.loc[0, "Evaluated"] = False
    with pytest.raises(ValueError, match="must be evaluable"):
        vr.build_rule_validation_results(specification, bad, executed)


def test_performance_rejects_string_booleans_and_ambiguous_study_names(observed_activity, executed):
    specification = pd.DataFrame([{"Rule ID": "R1", "Rule name": "Survey time"}])
    bad = observed_activity.copy()
    bad["Triggered"] = bad["Triggered"].astype(str)
    with pytest.raises(ValueError, match="Boolean"):
        vr.build_rule_validation_results(specification, bad, executed)
    bad = observed_activity.copy()
    bad.loc[0, "Study name"] = "Another study"
    with pytest.raises(ValueError, match="multiple study names"):
        vr.build_rule_validation_results(specification, bad, executed)


def test_performance_does_not_report_failed_rules_as_usable(observed_activity, executed):
    specification = pd.DataFrame([{"Rule ID": "R1", "Rule name": "Survey time"}])
    failed = executed.copy()
    failed.loc[failed["Rule ID"].eq("R1"), "Pass or fail"] = "Fail"
    result = vr.build_rule_validation_results(specification, observed_activity, failed)
    assert result["Regression-test status"].eq("Fail").all()
    assert result["Recommendation"].eq("Do not use failed rule in production").all()
