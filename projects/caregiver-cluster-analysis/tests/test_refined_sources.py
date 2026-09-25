"""Input integrity checks using synthetic records only; no credentials/network."""
from pathlib import Path

import pandas as pd
import pytest
import yaml

import refined_sources as rs


def field(name, kind="text", choices="", form="eligibility", annotation=""):
    return {"field_name": name, "field_type": kind, "form_name": form,
            "select_choices_or_calculations": choices, "branching_logic": "",
            "field_annotation": annotation}


def test_token_identity_must_match_both_pid_and_reference_title():
    study = {"project_id": 5749, "study_name": "Bilingual study"}
    rs.validate_project_identity({"project_id": 5749, "project_title": "Bilingual study"}, study)
    with pytest.raises(ValueError, match="Token mapping failed"):
        rs.validate_project_identity({"project_id": 4797, "project_title": "Bilingual study"}, study)
    with pytest.raises(ValueError, match="title"):
        rs.validate_project_identity({"project_id": 5749, "project_title": "Another study"}, study)
    with pytest.raises(ValueError, match="repeating"):
        rs.validate_project_identity({"project_id": 5749, "project_title": "Bilingual study",
                                      "has_repeating_instruments_or_events": 1}, study)


def test_test_exclusion_scans_raw_spanish_before_folding_with_word_boundaries():
    records = pd.DataFrame({"record_id": ["1", "2", "3", "4"],
                            "comment": ["caregiver", "contest", "testing", "a clinical TEST."],
                            "comment_s": ["a test response", "", "", ""]})
    metadata = pd.DataFrame([field("comment", "notes"), field("comment_s", "notes")])
    kept, audit = rs.filter_source_records(records, metadata, 4797)
    assert kept.record_id.tolist() == ["2", "3"]
    assert audit.record_id.tolist() == ["1", "4"]
    assert audit.matched_text_fields.tolist() == ["comment_s", "comment"]
    assert "a clinical TEST." not in audit.to_json()
    assert "a test response" not in audit.to_json()


def test_5749_archive_and_test_protocol_are_separate_and_boundaries_inclusive():
    records = pd.DataFrame({"record_id": ["1", "445", "446", "473", "474", "475"],
                            "comment": ["test", "", "", "", "caregiver", "test"]})
    kept, audit = rs.filter_source_records(records, pd.DataFrame([field("comment")]), 5749)
    assert kept.record_id.tolist() == ["474"]
    assert audit.archive_clone.sum() == 2
    assert audit.prelaunch_protocol.sum() == 2
    assert audit.contains_test_word.sum() == 2
    assert len(audit) == 5  # Overlapping exclusion reasons never double-count a record.


@pytest.mark.parametrize("ids", [["1", "1"], [""], ["1.0"], ["abc"], ["0"]])
def test_5749_ambiguous_record_keys_fail(ids):
    with pytest.raises(ValueError):
        rs.filter_source_records(pd.DataFrame({"record_id": ids}), pd.DataFrame([field("record_id")]), 5749)


def bilingual_metadata():
    return pd.DataFrame([
        field("english_spanish", "radio", "1, Español | 2, English"),
        field("elig_knee", "checkbox", "1, Light | 2, Band-Aid"),
        field("elig_knee_s", "checkbox", "1, Luz | 2, Curita"),
        field("pronouns_elig", "radio", "1, He | 2, She"),
        field("pronouns_elig_s", "radio", "1, Él | 2, Ella"),
    ])


def test_spanish_checkbox_selection_survives_exported_english_zeros():
    records = pd.DataFrame({"record_id": ["474"], "english_spanish": ["1"],
        "elig_knee___1": ["0"], "elig_knee___2": ["0"],
        "elig_knee_s___1": ["0"], "elig_knee_s___2": ["1"],
        "pronouns_elig": [""], "pronouns_elig_s": ["2"]})
    folded, metadata, _, audit = rs.fold_verified_twins(records, bilingual_metadata(), 5749)
    assert folded.loc[0, "elig_knee___2"] == "1"
    assert folded.loc[0, "pronouns_elig"] == "2"
    assert not folded.source_mapping_issue.any()
    assert "elig_knee_s" not in set(metadata.field_name)
    assert audit.answers_from_spanish.sum() == 2


def test_language_codes_come_from_metadata_and_conflicts_force_review():
    records = pd.DataFrame({"record_id": ["474", "475"], "english_spanish": ["1", "2"],
        "elig_knee___1": ["1", "0"], "elig_knee___2": ["0", "1"],
        "elig_knee_s___1": ["0", "1"], "elig_knee_s___2": ["1", "0"],
        "pronouns_elig": ["1", "2"], "pronouns_elig_s": ["2", "1"]})
    folded, _, _, audit = rs.fold_verified_twins(records, bilingual_metadata(), 5749)
    assert folded["elig_knee___2"].tolist() == ["1", "1"]
    assert folded["pronouns_elig"].tolist() == ["2", "2"]
    assert folded.source_mapping_issue.all()
    assert audit.conflicting_answers.sum() == 4


def test_non_matching_choice_codes_cannot_be_folded():
    metadata = pd.DataFrame([field("q", "radio", "1, Yes | 0, No"), field("q_s", "radio", "1, Sí | 2, No")])
    records = pd.DataFrame({"record_id": ["474"], "q": [""], "q_s": ["1"]})
    with pytest.raises(ValueError, match="cannot safely fold"):
        rs.fold_verified_twins(records, metadata, 5749)


def test_unknown_stored_choice_codes_are_source_review_issues():
    metadata = pd.DataFrame([field("fif_child_needs", "radio", "1, Yes | 0, No")])
    records = pd.DataFrame({"record_id": ["1", "2"], "fif_child_needs": ["0", "9"]})
    folded, _, mapping, _ = rs.fold_verified_twins(records, metadata, 4797)
    assert folded.source_mapping_issue.tolist() == [False, True]
    assert mapping.loc[mapping.source_field.eq("fif_child_needs"), "invalid_response_count"].item() == 1


def test_yesno_type_overrides_stale_multicountry_choice_labels():
    metadata = pd.DataFrame([field("demo_country", "yesno", "1, US\n2, Canada\n3, Other"),
                             field("demo_country_s", "radio", "1, Sí | 0, No")])
    records = pd.DataFrame({"record_id": ["474"], "demo_country": [""], "demo_country_s": ["0"]})
    folded, _, _, _ = rs.fold_verified_twins(records, metadata, 5749)
    assert folded.loc[0, "demo_country"] == "0"
    assert not folded.source_mapping_issue.any()


def test_unmatched_suffix_is_preserved_and_hidden_insert_is_not_a_conflict():
    metadata = pd.DataFrame([field("novel_s"), field("generated_s", annotation="@HIDDEN @CALCTEXT(...)")])
    records = pd.DataFrame({"record_id": ["474"], "novel_s": ["answer"], "generated_s": ["ella"]})
    folded, _, _, _ = rs.fold_verified_twins(records, metadata, 5749)
    assert "novel_s" in folded and "novel" not in folded
    assert "novel_s" in folded.loc[0, "source_mapping_notes"]
    assert "generated_s" not in folded.loc[0, "source_mapping_notes"]


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "caregiver-cluster-analysis"
    project.mkdir(parents=True)
    titles = {4797: "CAN", 4581: "Global", 5749: "Bilingual", 4931: "ICIS"}
    pd.DataFrame([{"PID": pid, "Project Title": title, "Population": title,
                   "Valid?": "Mixed", "Notes": "", "Records (n)": "1"}
                  for pid, title in titles.items()]).to_csv(project / rs.REFERENCE_FILE, index=False)
    config = {"redcap": {"api_url_env": "REDCAP_API_URL", "projects": {
        f"study_{pid}": {"project_id": pid, "token_env": f"TOKEN_{pid}"} for pid in titles}}}
    (project / "config.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / ".env").write_text("REDCAP_API_URL=https://example.invalid/api/\n" +
        "\n".join(f"TOKEN_{pid}=synthetic-{pid}" for pid in titles))
    calls = []
    def fake_fetch(api_url, token, pid, content, timeout, retries):
        calls.append((pid, content))
        if content == "project":
            return {"project_id": pid, "project_title": titles[pid]}
        if content == "metadata":
            return [field("record_id"), field("comment", "notes")]
        if content == "record":
            return [{"record_id": "474" if pid == 5749 else "1", "comment": "caregiver"}]
        return [{"instrument_name": "eligibility", "instrument_label": "Eligibility"}]
    monkeypatch.setattr(rs, "_fetch", fake_fetch)
    return project, calls


def test_snapshot_verifies_all_identities_first_and_offline_reads_detect_corruption(fake_project):
    project, calls = fake_project
    result = rs.load_refined_sources(project)
    assert [content for _, content in calls[:4]] == ["project"] * 4
    assert len(result.records) == 4
    offline = rs.load_refined_sources(project, refresh=False)
    assert len(offline.records) == 4
    assert offline.provenance.source_mode.str.startswith("Explicit offline").all()
    (result.snapshot_dir / "4797_record.json").write_text("[]")
    with pytest.raises(ValueError, match="Missing or changed"):
        rs.load_refined_sources(project, refresh=False)


def test_failed_refresh_does_not_reuse_old_snapshot_or_replace_latest(fake_project, monkeypatch):
    project, _ = fake_project
    result = rs.load_refined_sources(project)
    latest = result.snapshot_dir.parent / "refined_latest_snapshot.json"
    before = latest.read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic access failure")
    monkeypatch.setattr(rs, "_fetch", fail)
    with pytest.raises(RuntimeError, match="access failure"):
        rs.load_refined_sources(project, refresh=True)
    assert latest.read_bytes() == before


def test_equal_code_sets_do_not_hide_reversed_decision_meanings():
    metadata = pd.DataFrame([
        field("pronouns_elig", "radio", "1, He/Him/His | 2, She/Her/Hers"),
        field("pronouns_elig_s", "radio", "1, Ella | 2, Él"),
    ])
    records = pd.DataFrame({"record_id": ["474"], "pronouns_elig": [""], "pronouns_elig_s": ["1"]})
    with pytest.raises(ValueError, match="Decision choice meaning differs"):
        rs.fold_verified_twins(records, metadata, 5749)


def test_translated_numeric_typo_flags_affected_spanish_responses_only():
    metadata = pd.DataFrame([
        field("english_spanish", "radio", "1, Español | 2, English"),
        field("tfa_autistic_us", "radio", "1, 1% of children | 4, 25% of children"),
        field("tfa_autistic_us_s", "radio", "1, 1% de los niños | 4, 225% de los niños"),
    ])
    records = pd.DataFrame({"record_id": ["474", "475"], "english_spanish": ["1", "2"],
                            "tfa_autistic_us": ["", "4"], "tfa_autistic_us_s": ["1", ""]})
    folded, _, mapping, _ = rs.fold_verified_twins(records, metadata, 5749)
    # All Spanish respondents to the item saw the flawed option, regardless of selection.
    assert folded.source_mapping_issue.tolist() == [True, False]
    assert "225%" in mapping.loc[mapping.source_field.eq("tfa_autistic_us_s"), "mapping_notes"].item()
    assert rs.bilingual_choice_issues("tfa_behavior_age", {"2": "Before 1 year"},
                                      {"2": "Antes del primer año"}) == []


def test_snapshot_does_not_survive_a_changed_reference(fake_project):
    project, _ = fake_project
    rs.load_refined_sources(project)
    reference = project / rs.REFERENCE_FILE
    reference.write_text(reference.read_text().replace("CAN", "CAN Registry"))
    with pytest.raises(ValueError, match="reference changed"):
        rs.load_refined_sources(project, refresh=False)


def test_wrong_live_token_identity_prevents_any_participant_export(fake_project, monkeypatch):
    project, calls = fake_project
    original = rs._fetch
    def wrong_pid(api_url, token, pid, content, timeout, retries):
        result = original(api_url, token, pid, content, timeout, retries)
        if content == "project" and pid == 5749:
            result["project_id"] = 4700
        return result
    monkeypatch.setattr(rs, "_fetch", wrong_pid)
    with pytest.raises(ValueError, match="Token mapping failed"):
        rs.load_refined_sources(project)
    assert all(content == "project" for _, content in calls)
    assert not (project / "Caregiver Outputs/restricted/cache/refined_latest_snapshot.json").exists()


def test_audit_files_are_published_with_snapshot_and_never_mutated_by_offline_read(fake_project):
    project, _ = fake_project
    result = rs.load_refined_sources(project)
    audit_path = result.snapshot_dir / "exclusions.csv"
    before = audit_path.read_bytes()
    rs.load_refined_sources(project, refresh=False)
    assert audit_path.read_bytes() == before
    audit_path.write_text("altered audit")
    with pytest.raises(ValueError, match="Missing or changed"):
        rs.load_refined_sources(project, refresh=False)
