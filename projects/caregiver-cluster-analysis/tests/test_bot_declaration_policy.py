"""Tests for the bot declaration policy table and the review queue CSV."""

import pandas as pd

from caregiver_analysis_pipeline import (
    _bot_declaration_policy_table,
    _dirty4581_review_queue,
)


def _make_fixtures() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build minimal synthetic fixtures for both functions."""
    # Four dirty_4581 records with varying Tier 1 overlap patterns:
    #   record "10": 3 hits (R1+R2+R8) -> auto_exclude
    #   record "20": 2 hits with logic rule (R2+R8) -> auto_exclude
    #   record "30": 2 hits timing-only (R1+R2) -> review_queue
    #   record "40": 1 hit (R2) -> conditional_pass
    #   record "50": 0 hits -> pass (tier 4)
    features = pd.DataFrame(
        {
            "source_project": [
                "dirty_4581", "dirty_4581", "dirty_4581",
                "dirty_4581", "dirty_4581",
            ],
            "project_id": [4581, 4581, 4581, 4581, 4581],
            "record_id": ["10", "20", "30", "40", "50"],
        },
        index=["4581_10", "4581_20", "4581_30", "4581_40", "4581_50"],
    )
    rules = pd.DataFrame(
        {
            "rule_R1": [True, False, True, False, False],
            "rule_R2": [True, True, True, True, False],
            "rule_R5": [False, False, False, False, False],
            "rule_R8": [True, True, False, False, False],
            "rule_R9": [False, False, False, False, False],
            "tier": [1, 1, 1, 1, 4],
            "suspicion_rule_count": [0, 1, 0, 2, 0],
        },
        index=features.index,
    )
    tier1_record_overlap = pd.DataFrame(
        {
            "source_project": features["source_project"].values,
            "project_id": features["project_id"].values,
            "record_id": features["record_id"].values,
            "rule_R1": rules["rule_R1"].values,
            "rule_R2": rules["rule_R2"].values,
            "rule_R5": rules["rule_R5"].values,
            "rule_R8": rules["rule_R8"].values,
            "rule_R9": rules["rule_R9"].values,
            "tier1_hit_count": [3, 2, 2, 1, 0],
            "tier1_any_hit": [True, True, True, True, False],
            "tier1_hard_candidate_ge2": [True, True, True, False, False],
            "tier1_hard_candidate_ge3": [True, False, False, False, False],
            "tier1_all_5_hits": [False, False, False, False, False],
            "tier1_rule_combo": ["R1+R2+R8", "R2+R8", "R1+R2", "R2", "none"],
        },
        index=features.index,
    )
    return features, rules, tier1_record_overlap


def test_bot_declaration_policy_table_covers_all_tiers() -> None:
    """The policy table must cover tiers 1 through 4."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    assert set(policy["tier"]) == {1, 2, 3, 4}


def test_bot_declaration_policy_table_has_required_columns() -> None:
    """The policy CSV must include action, gift-card, and rationale columns."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    required = {
        "tier", "tier_label", "tier1_hit_count_range",
        "suspicion_rule_count_range", "rule_mix_requirement",
        "action", "gift_card_eligible", "rationale", "dirty_4581_n",
    }
    assert required.issubset(set(policy.columns))


def test_bot_declaration_policy_never_auto_excludes_single_hit() -> None:
    """Single-hit Tier 1 records must NOT be auto-excluded."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    single_hit = policy.loc[policy["tier1_hit_count_range"] == "1"]
    assert len(single_hit) > 0
    assert (single_hit["action"] != "auto_exclude").all()


def test_bot_declaration_policy_auto_excludes_ge3() -> None:
    """Records with >=3 Tier 1 hits must be auto-excluded."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    ge3 = policy.loc[policy["tier1_hit_count_range"] == ">=3"]
    assert len(ge3) > 0
    assert (ge3["action"] == "auto_exclude").all()
    assert (ge3["gift_card_eligible"] == "no").all()


def test_review_queue_contains_only_dirty4581_ge2() -> None:
    """The review queue must contain only dirty_4581 records with >=2 hits."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    review = _dirty4581_review_queue(features, rules, overlap, policy)
    # Records 10 (3 hits), 20 (2 hits), 30 (2 hits) qualify; 40 (1 hit) does not
    assert len(review) == 3
    assert set(review["record_id"].astype(str)) == {"10", "20", "30"}


def test_review_queue_has_adjudication_columns() -> None:
    """The review CSV must have the three empty adjudication columns."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    review = _dirty4581_review_queue(features, rules, overlap, policy)
    for col in ["adjudication_decision", "adjudication_notes", "adjudicator"]:
        assert col in review.columns
        assert (review[col] == "").all()


def test_review_queue_action_assignment() -> None:
    """Actions must match the policy: logic+2 -> auto_exclude, timing-only+2 -> review_queue."""
    features, rules, overlap = _make_fixtures()
    policy = _bot_declaration_policy_table(rules, features, overlap)
    review = _dirty4581_review_queue(features, rules, overlap, policy)
    review = review.set_index("record_id")
    # Record 10: 3 hits -> auto_exclude
    assert review.loc["10", "policy_action"] == "auto_exclude"
    assert review.loc["10", "gift_card_eligible"] == "no"
    # Record 20: 2 hits with R8 -> auto_exclude
    assert review.loc["20", "policy_action"] == "auto_exclude"
    assert review.loc["20", "gift_card_eligible"] == "no"
    # Record 30: 2 hits timing-only (R1+R2) -> review_queue
    assert review.loc["30", "policy_action"] == "review_queue"
    assert review.loc["30", "gift_card_eligible"] == "pending_review"
