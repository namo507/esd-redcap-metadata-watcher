import os
from pathlib import Path

import pytest

from caregiver_analysis_pipeline import (
    engineer_behavioral_features_and_rules,
    load_config,
    load_redcap_sources,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_DIR.parents[1]

# Every test here goes through load_redcap_sources, which reads the repository
# .env and pulls from REDCap unless a same-day cache is already on disk. That
# makes them integration tests against a live, credentialed API, not unit
# tests, and there is no honest way to run them on a machine without access.
#
# They were failing rather than skipping in CI, which reads as a broken build
# instead of an absent credential, and hid a genuinely broken workflow behind
# noise. Anyone with a .env still runs them exactly as before.
pytestmark = pytest.mark.skipif(
    not os.environ.get("REDCAP_API_URL")
    and not (REPOSITORY_ROOT / ".env").exists(),
    reason="needs REDCap credentials: set REDCAP_API_URL or add a repository .env",
)


def _load_upgrade_state():
    config = load_config(PROJECT_DIR)
    bundle = load_redcap_sources(PROJECT_DIR, config)
    features, rules, rule_definitions, false_positive, context = (
        engineer_behavioral_features_and_rules(bundle, config)
    )
    return bundle, features, rules, rule_definitions, false_positive, context


def test_branching_audit_rule_definition_mentions_new_scope() -> None:
    _, _, _, rule_definitions, _, _ = _load_upgrade_state()
    r8 = rule_definitions.loc[rule_definitions["rule"].eq("R8")].iloc[0]
    assert "branching contradictions" in r8["justification"]


def test_branching_audit_finds_expected_family_counts() -> None:
    _, _, rules, _, _, context = _load_upgrade_state()
    branching_audit = context["branching_audit"]
    family_counts = branching_audit.groupby("family").size().to_dict()
    assert family_counts["child_age_band_count_exceeds_child_count"] == 9
    assert family_counts["prenatal_testing_mutually_exclusive_pair"] == 8
    assert family_counts["prenatal_tested_reason_without_prenatal_yes"] == 16
    assert family_counts["prenatal_not_tested_reason_without_prenatal_no"] == 6
    assert family_counts["earlier_diagnosis_yes_reason_without_yes_gate"] == 2
    assert family_counts["earlier_diagnosis_no_reason_without_no_gate"] == 3
    assert family_counts.get("autistic_child_followup_hidden_answer", 0) == 0
    assert rules["rule_R8"].sum() >= len(branching_audit["uid"].unique())


def test_branching_audit_matches_record_fixtures() -> None:
    _, _, _, _, _, context = _load_upgrade_state()
    branching_audit = context["branching_audit"]
    actual = {
        family: sorted(
            frame["record_id"].astype(str).tolist(), key=lambda value: int(value)
        )
        for family, frame in branching_audit.groupby("family", sort=False)
    }
    assert actual["prenatal_testing_mutually_exclusive_pair"] == [
        "31",
        "118",
        "273",
        "571",
        "768",
        "1076",
        "1554",
        "1699",
    ]
    assert actual["prenatal_tested_reason_without_prenatal_yes"] == [
        "966",
        "967",
        "968",
        "969",
        "970",
        "971",
        "972",
        "973",
        "974",
        "975",
        "976",
        "977",
        "978",
        "979",
        "981",
        "1591",
    ]
    assert actual["prenatal_not_tested_reason_without_prenatal_no"] == [
        "364",
        "416",
        "987",
        "1026",
        "1707",
        "1741",
    ]
    assert actual["earlier_diagnosis_yes_reason_without_yes_gate"] == [
        "205",
        "1025",
    ]
    assert actual["earlier_diagnosis_no_reason_without_no_gate"] == [
        "1137",
        "1642",
        "1678",
    ]


def test_branching_audit_keeps_clean_reference_clear_for_new_families() -> None:
    _, features, _, _, false_positive, context = _load_upgrade_state()
    branching_audit = context["branching_audit"]
    new_families = branching_audit["family"].isin(
        [
            "prenatal_testing_mutually_exclusive_pair",
            "prenatal_tested_reason_without_prenatal_yes",
            "prenatal_not_tested_reason_without_prenatal_no",
            "earlier_diagnosis_yes_reason_without_yes_gate",
            "earlier_diagnosis_no_reason_without_no_gate",
        ]
    )
    assert branching_audit.loc[new_families, "source_project"].eq("dirty_4581").all()
    assert false_positive.loc[false_positive["rule"].eq("R8"), "verified_human_n"].iloc[0] == 131
    clean_uids = set(features.index[features["source_project"].eq("clean_4797")])
    assert clean_uids.isdisjoint(set(branching_audit.loc[new_families, "uid"]))


def test_branching_audit_captures_checkbox_child_columns() -> None:
    _, _, _, _, _, context = _load_upgrade_state()
    branching_audit = context["branching_audit"]
    tested_reason_row = branching_audit.loc[
        branching_audit["family"].eq("prenatal_tested_reason_without_prenatal_yes")
    ].iloc[0]
    assert "fif_tested_reason___1" in tested_reason_row["observed_fields"]
    assert "fif_prenatal_preg" in tested_reason_row["gating_fields"]
    assert "fif_prenatal_no_preg" in tested_reason_row["gating_fields"]