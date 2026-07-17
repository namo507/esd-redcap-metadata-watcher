import unittest

import pandas as pd

from watcher_core import (
    MISSING_PROFILE_COLUMNS,
    OVERLAP_COLUMNS,
    build_missing_profile,
    build_overlap_summary,
    build_project_summary,
    compare_projects,
    normalize_metadata,
)


def metadata_row(
    field_name,
    label,
    *,
    form="intake",
    field_type="text",
    validation="",
    identifier="",
    required="",
    branching="",
    matrix="",
    annotation="",
):
    return {
        "field_name": field_name,
        "form_name": form,
        "field_type": field_type,
        "field_label": label,
        "text_validation_type_or_show_slider_number": validation,
        "identifier": identifier,
        "required_field": required,
        "branching_logic": branching,
        "matrix_group_name": matrix,
        "field_annotation": annotation,
    }


def normalized(*rows):
    return normalize_metadata(pd.DataFrame(list(rows)))


class NormalizeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.raw = pd.DataFrame(
            {
                "form_name": ["visit", "symptoms"],
                "field_type": ["text", "checkbox"],
                "field_label": ["<b>Date of evaluation</b>", " &nbsp; <br> "],
                "text_validation_type_or_show_slider_number": ["date_ymd", ""],
                "identifier": ["Y", ""],
                "required_field": [" y ", ""],
                "branching_logic": ["", "[age] >= 2"],
                "matrix_group_name": ["", "symptom_matrix"],
                "field_annotation": [
                    "@HIDDEN @READONLY @HIDDEN",
                    "@CALCTEXT([score])",
                ],
            },
            index=pd.Index([" Visit_Date ", "symptoms"], name="field_name"),
        )
        self.exports = pd.DataFrame(
            {
                "choice_value": ["", "1", "2"],
                "export_field_name": [
                    "visit_date",
                    "symptoms___1",
                    "symptoms___2",
                ],
            },
            index=pd.Index(
                ["visit_date", "symptoms", "symptoms"],
                name="original_field_name",
            ),
        )

    def test_index_fix_export_expansion_and_derived_flags(self):
        result = normalize_metadata(self.raw, self.exports)

        self.assertEqual(result["field_key"].tolist(), ["visit_date", "symptoms", "symptoms"])
        self.assertEqual(
            result["export_field_name"].tolist(),
            ["visit_date", "symptoms___1", "symptoms___2"],
        )
        self.assertEqual(result["design_row_id"].nunique(), 2)

        visit = result.loc[result["field_key"] == "visit_date"].iloc[0]
        self.assertTrue(bool(visit["is_required"]))
        self.assertTrue(bool(visit["is_validated"]))
        self.assertTrue(bool(visit["is_identifier"]))
        self.assertTrue(bool(visit["is_doe_doc"]))
        self.assertEqual(visit["field_prefix"], "visit")
        self.assertEqual(visit["action_tags"], "@HIDDEN; @READONLY")
        self.assertTrue(bool(visit["tag_hidden"]))
        self.assertTrue(bool(visit["tag_readonly"]))
        self.assertEqual(visit["field_type_detail"], "text:date_ymd")

        symptoms = result.loc[result["field_key"] == "symptoms"].iloc[0]
        self.assertTrue(bool(symptoms["missing_label"]))
        self.assertTrue(bool(symptoms["has_branching"]))
        self.assertTrue(bool(symptoms["is_matrix"]))
        self.assertTrue(bool(symptoms["tag_calctext"]))

    def test_bad_configurable_regex_is_ignored(self):
        result = normalize_metadata(self.raw, self.exports, doe_doc_patterns=["["])
        self.assertFalse(result["is_doe_doc"].any())

    def test_missing_export_mapping_falls_back_to_design_name(self):
        raw = pd.DataFrame([metadata_row("record_id", "Record ID")])
        exports = pd.DataFrame(
            [{"original_field_name": "other", "export_field_name": "other"}]
        )
        result = normalize_metadata(raw, exports)
        self.assertEqual(result.loc[0, "export_field_name"], "record_id")

    def test_empty_metadata_has_a_stable_schema(self):
        result = normalize_metadata(pd.DataFrame())
        self.assertTrue(result.empty)
        for column in ("field_name", "field_key", "export_field_name", "missing_label"):
            self.assertIn(column, result.columns)


class ProjectSummaryTests(unittest.TestCase):
    def test_summary_counts_design_fields_not_checkbox_choices(self):
        raw = pd.DataFrame(
            [
                metadata_row(
                    "visit_date",
                    "Visit date",
                    validation="date_ymd",
                    identifier="y",
                    required="y",
                ),
                metadata_row(
                    "symptoms",
                    "",
                    form="symptoms",
                    field_type="checkbox",
                    branching="[age] > 1",
                    matrix="symptom_group",
                    annotation="@HIDDEN",
                ),
            ]
        ).set_index("field_name")
        exports = pd.DataFrame(
            [
                {"original_field_name": "visit_date", "choice_value": "", "export_field_name": "visit_date"},
                {"original_field_name": "symptoms", "choice_value": "1", "export_field_name": "symptoms___1"},
                {"original_field_name": "symptoms", "choice_value": "2", "export_field_name": "symptoms___2"},
            ]
        ).set_index("original_field_name")
        frame = normalize_metadata(raw, exports)
        summary = build_project_summary(
            frame,
            project_info={
                "project_title": "Study",
                "project_id": 42,
                "is_longitudinal": "1",
                "has_repeating_instruments_or_events": "1",
            },
            instruments=[
                {"instrument_name": "intake"},
                {"instrument_name": "symptoms"},
            ],
            events=[
                {"unique_event_name": "baseline_arm_1"},
                {"unique_event_name": "followup_arm_1"},
            ],
            repeating=[{"form_name": "symptoms"}],
        )

        self.assertEqual(summary["project_title"], "Study")
        self.assertEqual(summary["project_id"], "42")
        self.assertEqual(summary["instrument_count"], 2)
        self.assertEqual(summary["design_field_count"], 2)
        self.assertEqual(summary["export_field_count"], 3)
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["required_field_count"], 1)
        self.assertEqual(summary["branching_field_count"], 1)
        self.assertEqual(summary["validated_field_count"], 1)
        self.assertEqual(summary["matrix_field_count"], 1)
        self.assertEqual(summary["matrix_group_count"], 1)
        self.assertEqual(summary["identifier_field_count"], 1)
        self.assertEqual(summary["doe_doc_field_count"], 1)
        self.assertEqual(summary["missing_label_count"], 1)
        self.assertTrue(summary["has_repeating"])

    def test_empty_classic_project_returns_zeroes_and_no_event_count(self):
        summary = build_project_summary(
            normalize_metadata(pd.DataFrame()),
            project_info={"is_longitudinal": "0"},
        )
        self.assertEqual(summary["design_field_count"], 0)
        self.assertEqual(summary["export_field_count"], 0)
        self.assertIsNone(summary["event_count"])
        self.assertFalse(summary["is_longitudinal"])


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.projects = {
            "NANO": normalized(
                metadata_row(" Shared ", "Shared label"),
                metadata_row("ref_only", "Reference only"),
                metadata_row("type_diff", "Type", field_type="text"),
                metadata_row("label_diff", "Caregiver age"),
                metadata_row("form_diff", "Same", form="form_a"),
                metadata_row("validation_gap", "Validated", validation="integer"),
                metadata_row("identifier_diff", "Identifier", identifier="y"),
                metadata_row("visit_date", "Visit date", validation="date_ymd"),
                metadata_row("mother_age", "Mother age"),
                metadata_row("blank_label", ""),
            ),
            "IPSA": normalized(
                metadata_row("shared", " shared   label "),
                metadata_row("type_diff", "Type", field_type="dropdown"),
                metadata_row("label_diff", "Parent age"),
                metadata_row("form_diff", "Same", form="form_b"),
                metadata_row("validation_gap", "Validated"),
                metadata_row("identifier_diff", "Identifier"),
                metadata_row("visit_date", "Visit date", validation="date_ymd"),
                metadata_row("maternal_age", "Mother age"),
                metadata_row("nonref_partial", "Partial"),
            ),
            "ACTION": normalized(
                metadata_row("shared", "SHARED LABEL"),
                metadata_row("type_diff", "Type", field_type="text"),
                metadata_row("label_diff", "Caregiver age"),
                metadata_row("form_diff", "Same", form="form_a"),
                metadata_row("validation_gap", "Validated", validation="integer"),
                metadata_row("identifier_diff", "Identifier", identifier="y"),
                metadata_row("nonref_partial", "Partial"),
            ),
        }

    def row(self, comparison, key):
        return comparison.set_index("field_key").loc[key]

    def test_exact_key_comparison_and_taxonomy(self):
        comparison = compare_projects(self.projects, reference_project="NANO")

        self.assertEqual(self.row(comparison, "shared")["discrepancy_category"], "ALIGNED")
        self.assertEqual(
            self.row(comparison, "ref_only")["discrepancy_category"],
            "MISSING_VS_REFERENCE",
        )
        self.assertEqual(
            self.row(comparison, "nonref_partial")["discrepancy_category"],
            "PARTIAL_PRESENCE",
        )
        self.assertEqual(
            self.row(comparison, "type_diff")["discrepancy_category"],
            "TYPE_MISMATCH",
        )
        self.assertEqual(
            self.row(comparison, "label_diff")["discrepancy_category"],
            "LABEL_MISMATCH",
        )
        self.assertEqual(
            self.row(comparison, "form_diff")["discrepancy_category"],
            "INSTRUMENT_MISMATCH",
        )
        self.assertEqual(
            self.row(comparison, "validation_gap")["discrepancy_category"],
            "VALIDATION_GAP",
        )

        type_row = self.row(comparison, "type_diff")
        self.assertEqual(type_row["type_variant_count"], 2)
        self.assertEqual(type_row["type_mismatch"], 1)
        self.assertEqual(type_row["mismatch_total"], 1)
        self.assertTrue(bool(type_row["in_NANO"]))
        self.assertTrue(bool(type_row["in_IPSA"]))
        self.assertTrue(bool(type_row["in_ACTION"]))

        identifier_row = self.row(comparison, "identifier_diff")
        self.assertTrue(bool(identifier_row["identifier_flag_mismatch"]))
        self.assertEqual(identifier_row["discrepancy_category"], "ALIGNED")

    def test_no_label_similarity_inference_or_auto_merge(self):
        comparison = compare_projects(self.projects, reference_project="NANO")
        keys = set(comparison["field_key"])
        self.assertIn("mother_age", keys)
        self.assertIn("maternal_age", keys)
        self.assertEqual(len(comparison.loc[comparison["field_key"].isin({"mother_age", "maternal_age"})]), 2)
        self.assertNotIn("LIKELY_SAME_CONCEPT_DIFF_NAME", set(comparison["discrepancy_category"]))

    def test_reference_falls_back_deterministically_to_first_connected_project(self):
        comparison = compare_projects(self.projects, reference_project="NOT_CONNECTED")
        self.assertEqual(set(comparison["reference_project"]), {"NANO"})

    def test_empty_comparison_has_stable_columns(self):
        comparison = compare_projects({})
        self.assertTrue(comparison.empty)
        self.assertIn("discrepancy_category", comparison.columns)


class OverlapAndMissingProfileTests(unittest.TestCase):
    def test_overlap_summary_counts_and_exact_members(self):
        projects = {
            "A": normalized(
                metadata_row("a", "A"),
                metadata_row("b", "B"),
                metadata_row("c", "C"),
            ),
            "B": normalized(
                metadata_row("b", "B"),
                metadata_row("c", "C"),
                metadata_row("d", "D"),
            ),
            "C": normalized(
                metadata_row("c", "C"),
                metadata_row("d", "D"),
                metadata_row("e", "E"),
            ),
        }
        overlap = build_overlap_summary(projects, reference_project="A")

        totals = overlap.loc[overlap["metric"] == "TOTAL_DISTINCT"].set_index("project")
        self.assertEqual(totals["count"].to_dict(), {"A": 3, "B": 3, "C": 3})
        common = overlap.loc[overlap["metric"] == "COMMON_ALL"].iloc[0]
        self.assertEqual(common["count"], 1)
        self.assertEqual(common["field_keys"], "c")

        pairs = overlap.loc[overlap["metric"] == "PAIR_COMMON"].set_index("scope")
        self.assertEqual(pairs.loc["A & B", "count"], 2)
        self.assertEqual(pairs.loc["A & C", "count"], 1)
        self.assertEqual(pairs.loc["B & C", "count"], 2)

        unique = overlap.loc[overlap["metric"] == "UNIQUE_TO_PROJECT"].set_index("project")
        self.assertEqual(unique.loc["A", "field_keys"], "a")
        self.assertEqual(unique.loc["B", "count"], 0)
        self.assertEqual(unique.loc["C", "field_keys"], "e")

        missing = overlap.loc[overlap["metric"] == "MISSING_VS_REFERENCE"].set_index("project")
        self.assertEqual(missing.loc["B", "field_keys"], "a")
        self.assertEqual(missing.loc["C", "field_keys"], "a | b")

    def test_missing_profile_is_field_and_project_attributed(self):
        projects = {
            "NANO": normalized(
                metadata_row("blank", ""),
                metadata_row("score", "Score", validation="integer"),
                metadata_row("visit_date", "Visit date", validation="date_ymd"),
                metadata_row("record_id", "Record", identifier="y"),
            ),
            "IPSA": normalized(
                metadata_row("score", "Score"),
                metadata_row("visit_date", "Visit date", validation="date_ymd"),
                metadata_row("record_id", "Record"),
            ),
            "ACTION": normalized(
                metadata_row("score", "Score", validation="integer"),
                metadata_row("record_id", "Record", identifier="y"),
            ),
        }
        profile = build_missing_profile(projects)

        blank = profile.loc[profile["issue_type"] == "MISSING_LABEL"]
        self.assertEqual(blank[["project", "field_key"]].values.tolist(), [["NANO", "blank"]])

        validation = profile.loc[profile["issue_type"] == "VALIDATION_GAP"]
        self.assertEqual(validation[["project", "field_key"]].values.tolist(), [["IPSA", "score"]])
        self.assertEqual(validation.iloc[0]["compared_with"], "NANO, ACTION")

        doe = profile.loc[profile["issue_type"] == "DOE_DOC_MISSING"]
        self.assertEqual(doe[["project", "field_key"]].values.tolist(), [["ACTION", "visit_date"]])
        self.assertEqual(doe.iloc[0]["compared_with"], "NANO, IPSA")

        identifiers = profile.loc[profile["issue_type"] == "IDENTIFIER_FLAG_MISMATCH"]
        self.assertEqual(set(identifiers["project"]), {"NANO", "IPSA", "ACTION"})
        self.assertTrue((identifiers["field_key"] == "record_id").all())

    def test_empty_overlap_and_profile_have_stable_columns(self):
        overlap = build_overlap_summary({})
        profile = build_missing_profile({})
        self.assertEqual(tuple(overlap.columns), OVERLAP_COLUMNS)
        self.assertEqual(tuple(profile.columns), MISSING_PROFILE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
