import pandas as pd

from caregiver_analysis_pipeline import _tier1_rule_overlap_tables


def test_tier1_overlap_tables_capture_all_and_some_hits() -> None:
    features = pd.DataFrame(
        {
            "source_project": ["clean_4797", "clean_4797", "dirty_4581", "dirty_4581"],
            "project_id": [4797, 4797, 4581, 4581],
            "record_id": ["1", "2", "3", "4"],
        },
        index=["4797_1", "4797_2", "4581_3", "4581_4"],
    )
    rules = pd.DataFrame(
        {
            "rule_R1": [True, False, True, False],
            "rule_R2": [True, False, False, False],
            "rule_R5": [True, False, False, False],
            "rule_R8": [True, False, True, False],
            "rule_R9": [True, False, False, False],
        },
        index=features.index,
    )

    overlap, combo_summary, project_summary = _tier1_rule_overlap_tables(features, rules)

    all_hits = overlap.loc[overlap["record_id"].eq("1")].iloc[0]
    assert bool(all_hits["tier1_all_5_hits"])
    assert int(all_hits["tier1_hit_count"]) == 5
    assert all_hits["tier1_rule_combo"] == "R1+R2+R5+R8+R9"

    some_hits = overlap.loc[overlap["record_id"].eq("3")].iloc[0]
    assert bool(some_hits["tier1_any_hit"])
    assert bool(some_hits["tier1_hard_candidate_ge2"])
    assert not bool(some_hits["tier1_hard_candidate_ge3"])
    assert int(some_hits["tier1_hit_count"]) == 2
    assert some_hits["tier1_rule_combo"] == "R1+R8"

    no_hits = overlap.loc[overlap["record_id"].eq("4")].iloc[0]
    assert int(no_hits["tier1_hit_count"]) == 0
    assert no_hits["tier1_rule_combo"] == "none"

    dirty_combo_counts = combo_summary.loc[
        combo_summary["source_project"].eq("dirty_4581")
    ].set_index("tier1_rule_combo")["n_records"]
    assert int(dirty_combo_counts["R1+R8"]) == 1
    assert int(dirty_combo_counts["none"]) == 1

    clean_summary = project_summary.loc[
        project_summary["source_project"].eq("clean_4797")
    ].iloc[0]
    assert int(clean_summary["tier1_any_hit_n"]) == 1
    assert int(clean_summary["tier1_ge3_n"]) == 1
    assert int(clean_summary["tier1_all5_n"]) == 1
