"""Verified, private REDCap inputs for the refined caregiver review workbook.

Live refresh is the default. All configured tokens are verified against REDCap's
project endpoint and the supplied study reference before records are exported.
Only a fully verified four-study snapshot is published. An offline read must be
explicit and validates every cached file's SHA-256 digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from typing import Any

import pandas as pd
import requests
from dotenv import dotenv_values
import yaml

REFERENCE_FILE = "Survey Summary and Notes 07222026(Sheet1).csv"
TEST_PATTERN = re.compile(r"\btest\b", re.IGNORECASE)
TWIN_PATTERN = re.compile(r"^(?P<base>.+)_(?:s|sp)$")
EXPECTED_PIDS = {4797, 4581, 5749, 4931}
STUDY_ORDER = {4797: (1, "CAN"), 4581: (2, "Global"), 5749: (3, "Bilingual"), 4931: (4, "ICIS")}
RULE_FIELDS = ("elig_knee", "pronouns_elig", "demo_gender", "fif_child_needs", "fif_num_autistic")
CATEGORICAL = {"radio", "dropdown", "checkbox", "yesno", "truefalse"}


@dataclass
class RefinedSources:
    records: pd.DataFrame
    records_by_pid: dict[int, pd.DataFrame]
    metadata_by_pid: dict[int, pd.DataFrame]
    raw_metadata_by_pid: dict[int, pd.DataFrame]
    provenance: pd.DataFrame
    exclusions: pd.DataFrame
    field_mapping: pd.DataFrame
    folding_audit: pd.DataFrame
    registry: dict[int, dict[str, Any]]
    snapshot_dir: Path


def _strings(values: pd.Series) -> pd.Series:
    return values.astype("string").fillna("").str.strip()


def choice_map(field: dict[str, Any] | pd.Series) -> dict[str, str]:
    """REDCap yes/no and true/false fields use implicit codes, ignoring stale labels."""
    kind = field.get("field_type", "")
    if kind == "yesno":
        return {"1": "Yes", "0": "No"}
    if kind == "truefalse":
        return {"1": "True", "0": "False"}
    if kind not in {"radio", "dropdown", "checkbox"}:
        return {}
    result: dict[str, str] = {}
    for part in str(field.get("select_choices_or_calculations", "")).split("|"):
        code, separator, label = part.strip().partition(",")
        if separator:
            result[code.strip()] = label.strip()
    return result


def load_study_registry(project_dir: Path) -> dict[int, dict[str, Any]]:
    config = yaml.safe_load((project_dir / "config.yaml").read_text())["redcap"]["projects"]
    reference = pd.read_csv(project_dir / REFERENCE_FILE, dtype=str).fillna("")
    reference = reference.loc[reference["PID"].str.fullmatch(r"\d+")]
    if reference["PID"].duplicated().any():
        raise ValueError("The study reference contains duplicate PIDs.")
    reference = reference.set_index("PID")
    registry: dict[int, dict[str, Any]] = {}
    for source_project, settings in config.items():
        pid = int(settings["project_id"])
        if pid in registry or str(pid) not in reference.index or pid not in STUDY_ORDER:
            raise ValueError(f"Study PID {pid} is duplicated, unreferenced, or outside the four-study review.")
        row = reference.loc[str(pid)]
        order, short = STUDY_ORDER[pid]
        title, population = row["Project Title"].strip(), row["Population"].strip()
        registry[pid] = {
            "source_project": source_project, "project_id": pid,
            "study_name": title, "population": population,
            "reference_validity": row["Valid?"], "reference_notes": row["Notes"],
            "reference_record_count": row["Records (n)"],
            "display_label": f"Study {order} - {title} [{population}] (PID {pid})",
            "sheet_name": f"Study {order} - {short} (PID {pid})",
            "token_env": settings["token_env"],
        }
    if set(registry) != EXPECTED_PIDS:
        raise ValueError("Refined review requires exactly PIDs 4797, 4581, 5749, and 4931.")
    return dict(sorted(registry.items(), key=lambda item: STUDY_ORDER[item[0]][0]))


def validate_project_identity(project: dict[str, Any], study: dict[str, Any]) -> None:
    """Fail before exporting records when a token reaches the wrong project."""
    try:
        actual_pid = int(project.get("project_id", -1))
    except (TypeError, ValueError):
        actual_pid = -1
    expected_pid = study["project_id"]
    if actual_pid != expected_pid:
        raise ValueError(f"Token mapping failed: configured PID {expected_pid}, API PID {actual_pid}.")
    normalize = lambda value: " ".join(str(value).split())
    if normalize(project.get("project_title", "")) != normalize(study["study_name"]):
        raise ValueError(f"PID {expected_pid} API title does not match the supplied study reference.")
    if int(project.get("is_longitudinal", 0)) or int(project.get("has_repeating_instruments_or_events", 0)):
        raise ValueError(f"PID {expected_pid} requires a longitudinal/repeating-record key; refusing record-only joins.")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _private_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _fetch(api_url: str, token: str, pid: int, content: str, timeout: int, retries: int) -> Any:
    payload = {"token": token, "content": content, "format": "json", "returnFormat": "json"}
    if content == "record":
        payload.update(type="flat", rawOrLabel="raw", rawOrLabelHeaders="raw",
                       exportCheckboxLabel="false", exportSurveyFields="true",
                       exportDataAccessGroups="false")
    for attempt in range(retries):
        try:
            response = requests.post(api_url, data=payload, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            if response.status_code != 200:
                raise RuntimeError(f"REDCap {content} export failed for PID {pid} (HTTP {response.status_code}).")
            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(f"REDCap returned non-JSON {content} for PID {pid}.") from None
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"REDCap rejected {content} export for PID {pid}; verify token export privileges.")
            if content == "project":
                if not isinstance(data, dict):
                    raise RuntimeError(f"Malformed REDCap project identity for PID {pid}.")
            elif not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                raise RuntimeError(f"Malformed REDCap {content} export for PID {pid}.")
            if not data:
                raise RuntimeError(f"Empty REDCap {content} export for PID {pid}; no silent empty-study output.")
            return data
        except (requests.Timeout, requests.ConnectionError):
            if attempt + 1 >= retries:
                raise RuntimeError(f"REDCap {content} export unavailable for PID {pid}; no cached fallback used.") from None
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"REDCap {content} export exhausted retries for PID {pid}.")


def _assert_record_keys(records: pd.DataFrame, pid: int) -> None:
    if "record_id" not in records or records.empty:
        raise ValueError(f"PID {pid} did not export a nonempty record_id column.")
    ids = _strings(records["record_id"])
    if ids.eq("").any() or ids.duplicated().any():
        raise ValueError(f"PID {pid} exported blank or repeated record identifiers; refusing ambiguous joins.")
    if pid == 5749 and not ids.str.fullmatch(r"[1-9]\d*").all():
        raise ValueError("PID 5749 contains non-positive-integer IDs; partition protocol needs explicit review.")


def filter_source_records(records: pd.DataFrame, metadata: pd.DataFrame, pid: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scan unmodified text in both languages before partitioning and folding."""
    _assert_record_keys(records, pid)
    records = records.copy()
    records["record_id"] = _strings(records["record_id"])
    text_fields = metadata.loc[metadata["field_type"].isin(["text", "notes"]), "field_name"]
    missing = [name for name in text_fields if name not in records]
    if missing:
        raise ValueError(f"PID {pid} export omits {len(missing)} text fields; cannot apply universal test exclusion.")
    matched: dict[Any, list[str]] = {index: [] for index in records.index}
    for name in text_fields:
        for index in records.index[_strings(records[name]).str.contains(TEST_PATTERN, na=False)]:
            matched[index].append(name)
    excluded: list[dict[str, Any]] = []
    keep = pd.Series(True, index=records.index)
    for index, record_id in records["record_id"].items():
        reasons = []
        if pid == 5749:
            if 1 <= int(record_id) <= 445:
                reasons.append("Archived clone from PID 4700 (records 1-445)")
            elif 446 <= int(record_id) <= 473:
                reasons.append("Pre-launch internal test protocol (records 446-473)")
        if matched[index]:
            reasons.append("Standalone word 'test' in a text/notes response")
        if reasons:
            keep.at[index] = False
            excluded.append({"project_id": pid, "record_id": record_id,
                             "response_key": f"{pid}:{record_id}", "exclusion_reason": "; ".join(reasons),
                             "matched_text_fields": ", ".join(matched[index]),
                             "archive_clone": pid == 5749 and 1 <= int(record_id) <= 445,
                             "prelaunch_protocol": pid == 5749 and 446 <= int(record_id) <= 473,
                             "contains_test_word": bool(matched[index])})
    columns = ["project_id", "record_id", "response_key", "exclusion_reason", "matched_text_fields",
               "archive_clone", "prelaunch_protocol", "contains_test_word"]
    return records.loc[keep].copy(), pd.DataFrame(excluded, columns=columns)


def _language_codes(metadata: pd.DataFrame) -> tuple[str | None, str | None]:
    language = metadata.loc[metadata["field_name"].eq("english_spanish")]
    if language.empty:
        return None, None
    codes = choice_map(language.iloc[0])
    spanish = [code for code, label in codes.items() if re.search(r"espa[ñn]ol|spanish", label, re.I)]
    english = [code for code, label in codes.items() if re.search(r"english|ingl[eé]s", label, re.I)]
    if len(spanish) != 1 or len(english) != 1 or spanish == english:
        raise ValueError("Cannot uniquely derive English/Spanish routing from live metadata.")
    return spanish[0], english[0]


def _plain_label(label: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", str(label)))
    plain = unicodedata.normalize("NFKD", plain)
    plain = "".join(character for character in plain if not unicodedata.combining(character))
    return " ".join(plain.lower().split())


def _rule_choice_concept(field_name: str, label: str) -> str:
    """Recognize the decision fields' English/Spanish meanings, not just codes."""
    label = _plain_label(label)
    patterns = {
        "elig_knee": [
            ("bandage", r"band.?aid|curita|venda|aposito"),
            ("light", r"light|luz"), ("pool", r"pool|piscina"),
            ("homework", r"homework|tarea"), ("fan", r"\bfan\b|ventilador"),
            ("emergency", r"\ber\b|emergency|emergencia"),
            ("no_image", r"no image|no hay imagen"),
            ("prefer_not", r"prefer not|prefiero no"),
            ("unknown", r"don.t know|no se")],
        "pronouns_elig": [("he", r"^(?:he(?:/|$)|el$)"), ("she", r"^(?:she(?:/|$)|ella$)"),
                          ("they", r"^(?:they(?:/|$)|elle$)"), ("other", r"^other|^otr[oa]")],
        "demo_gender": [("man", r"^man$|^hombre$"), ("woman", r"^woman$|^mujer$"),
                        ("nonbinary", r"non.binary|no binari"), ("other", r"^other|^otr[oa]"),
                        ("prefer_not", r"prefer not|prefiero no")],
        "fif_child_needs": [("yes", r"^yes$|^si$"), ("no", r"^no$")],
        "fif_num_autistic": [("more_than_3", r"more than 3|mas de 3")],
    }
    for concept, pattern in patterns.get(field_name, []):
        if re.search(pattern, label):
            return concept
    return label


def bilingual_choice_issues(base: str, english: dict[str, str], spanish: dict[str, str]) -> list[str]:
    """Reject changed decision meanings and report numeric translation defects.

    Full linguistic equivalence is not inferred for arbitrary translated prose.
    The audit verifies structural codes for every twin, explicit concepts for
    R13-R15, and numeric anchors for other choices.
    """
    issues = []
    for code, en_label in english.items():
        sp_label = spanish[code]
        if base in RULE_FIELDS:
            if _rule_choice_concept(base, en_label) != _rule_choice_concept(base, sp_label):
                raise ValueError(f"Decision choice meaning differs between {base} and its Spanish twin (code {code}).")
        elif base == "demo_country":
            en_concept = _rule_choice_concept("fif_child_needs", en_label)
            sp_concept = _rule_choice_concept("fif_child_needs", sp_label)
            if en_concept != sp_concept:
                raise ValueError(f"US-residence choice meaning differs between English/Spanish (code {code}).")
        # Some translated labels redundantly start with their option number.
        def numbers(label: str) -> list[str]:
            plain = _plain_label(label)
            plain = re.sub(rf"^{re.escape(code)}\s+(?=si\b|yes\b|los\b)", "", plain)
            plain = re.sub(r"\b(?:first|primer|primero|primera)\b", "1", plain)
            return re.findall(r"\d+(?:[.,]\d+)*", plain)
        if numbers(en_label) != numbers(sp_label):
            issues.append(f"Numeric meaning differs in option {code}: English '{_plain_label(en_label)}'; Spanish '{_plain_label(sp_label)}'")
    return issues


def fold_verified_twins(records: pd.DataFrame, metadata: pd.DataFrame, pid: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fold true metadata twins at question level, including entire checkbox vectors.

    The selected language wins when both versions are populated; substantive
    conflicts are retained as review warnings. An untouched checkbox vector of
    exported zeros must never overwrite a selected Spanish answer.
    """
    records = records.copy()
    metadata = metadata.fillna("").copy()
    if metadata["field_name"].duplicated().any():
        raise ValueError(f"PID {pid} metadata contains repeated field names.")
    fields = metadata.set_index("field_name", drop=False)
    spanish_code, english_code = _language_codes(metadata)
    language = _strings(records.get("english_spanish", pd.Series("", index=records.index)))
    warnings: dict[Any, list[str]] = {index: [] for index in records.index}
    mapping, folding = [], []
    dropped_meta = []

    def warn(mask: pd.Series, message: str) -> None:
        for index in records.index[mask.fillna(False)]:
            warnings[index].append(message)

    for name, field in fields.iterrows():
        choices = choice_map(field)
        kind = field["field_type"]
        invalid = pd.Series(False, index=records.index)
        if kind in CATEGORICAL:
            columns = [f"{name}___{code}" for code in choices] if kind == "checkbox" else [name]
            if any(column not in records for column in columns):
                raise ValueError(f"PID {pid} record export lacks a metadata-declared response field: {name}.")
            for column in columns:
                allowed = {"0", "1"} if kind == "checkbox" else set(choices)
                values = _strings(records[column])
                invalid |= values.ne("") & ~values.isin(allowed)
            warn(invalid, f"Unsupported stored choice code in {name}")
        mapping.append({"project_id": pid, "source_field": name, "canonical_field": name,
                        "field_type": kind, "choice_codes": ", ".join(choices),
                        "mapping_status": "Live metadata validated",
                        "invalid_response_count": int(invalid.sum()), "mapping_notes": ""})

    for name, field in fields.iterrows():
        match = TWIN_PATTERN.match(name)
        if not match:
            continue
        base = match["base"]
        if base not in fields.index:
            # Spanish grammatical inserts generated by @CALCTEXT are not answers.
            annotation = str(field.get("field_annotation", ""))
            if name in records and field["field_type"] != "descriptive" and "@CALCTEXT" not in annotation.upper():
                warn(_strings(records[name]).ne(""), f"Spanish field has no verified English twin: {name}")
            continue
        english = fields.loc[base]
        en_choices, sp_choices = choice_map(english), choice_map(field)
        en_kind, sp_kind = english["field_type"], field["field_type"]
        compatible_kind = en_kind == sp_kind or ({en_kind, sp_kind} <= {"yesno", "radio", "dropdown"})
        if not compatible_kind or set(en_choices) != set(sp_choices) or english["form_name"] != field["form_name"]:
            raise ValueError(f"PID {pid} cannot safely fold {name}: metadata type, form, or choice codes differ.")
        semantic_issues = bilingual_choice_issues(base, en_choices, sp_choices)
        dropped_meta.append(name)
        for row in mapping:
            if row["source_field"] == name:
                row["canonical_field"] = base
                row["mapping_status"] = "Verified Spanish twin; same form and raw choice codes"
                if base in RULE_FIELDS or base == "demo_country":
                    row["mapping_status"] += "; decision-choice meanings verified"
                if semantic_issues:
                    row["mapping_status"] = "Spanish numeric choice label differs; affected responses require review"
                    row["mapping_notes"] = "; ".join(semantic_issues)
        if en_kind == "descriptive":
            continue
        en_columns = [f"{base}___{code}" for code in en_choices] if en_kind == "checkbox" else [base]
        sp_columns = [f"{name}___{code}" for code in sp_choices] if en_kind == "checkbox" else [name]
        if en_kind == "checkbox":
            # Keep choices aligned by code even if metadata order differs.
            sp_columns = [f"{name}___{code}" for code in en_choices]
        if any(column not in records for column in en_columns + sp_columns):
            raise ValueError(f"PID {pid} is missing stored columns for verified twin {name}.")
        en = records[en_columns].apply(_strings)
        sp = records[sp_columns].apply(_strings)
        sp.columns = en_columns
        if en_kind == "checkbox":
            en_answered, sp_answered = en.eq("1").any(axis=1), sp.eq("1").any(axis=1)
        else:
            en_answered, sp_answered = en.ne("").any(axis=1), sp.ne("").any(axis=1)
        prefer_spanish = language.eq(spanish_code) if spanish_code is not None else pd.Series(False, index=records.index)
        use_spanish = (prefer_spanish & sp_answered) | (~en_answered & sp_answered)
        if semantic_issues:
            warn(use_spanish, f"Numeric meaning differs between English/Spanish choice labels for {base}; verify source translation")
        conflict = en_answered & sp_answered & en.ne(sp).any(axis=1)
        if en_kind != "calc" and "@CALCTEXT" not in str(field.get("field_annotation", "")).upper():
            warn(conflict, f"English/Spanish answers differ in {base}")
        # Explicitly selected route with only a populated other-language answer is
        # preserved and flagged; the raw snapshot remains the complete evidence.
        route_mismatch = ((language.eq(english_code) & ~en_answered & sp_answered) |
                          (language.eq(spanish_code) & en_answered & ~sp_answered)) if spanish_code else pd.Series(False, index=records.index)
        if en_kind != "calc" and "@CALCTEXT" not in str(field.get("field_annotation", "")).upper():
            warn(route_mismatch, f"Answer only in non-selected language for {base}")
        records.loc[use_spanish, en_columns] = sp.loc[use_spanish].to_numpy()
        records = records.drop(columns=sp_columns)
        folding.append({"project_id": pid, "spanish_field": name, "canonical_field": base,
                        "answers_from_spanish": int(use_spanish.sum()),
                        "conflicting_answers": int(conflict.sum()),
                        "language_route_mismatches": int(route_mismatch.sum())})
    records["source_mapping_issue"] = [bool(warnings[index]) for index in records.index]
    records["source_mapping_notes"] = ["; ".join(warnings[index]) for index in records.index]
    folded_meta = metadata.loc[~metadata["field_name"].isin(dropped_meta)].reset_index(drop=True)
    for field in RULE_FIELDS:
        if field not in fields.index:
            mapping.append({"project_id": pid, "source_field": field, "canonical_field": field,
                            "field_type": "", "choice_codes": "", "mapping_status": "Not present in this study; rule not applicable",
                            "invalid_response_count": 0})
    return records, folded_meta, pd.DataFrame(mapping), pd.DataFrame(folding, columns=[
        "project_id", "spanish_field", "canonical_field", "answers_from_spanish", "conflicting_answers", "language_route_mismatches"])


def _prepare_sources(raw: dict[int, dict[str, Any]], registry: dict[int, dict[str, Any]], snapshot_dir: Path,
                     manifest: dict[str, Any], refresh: bool) -> RefinedSources:
    records_by_pid, metadata_by_pid, raw_metadata_by_pid = {}, {}, {}
    provenance, exclusions, mappings, foldings = [], [], [], []
    for pid, study in registry.items():
        entry = raw[pid]
        validate_project_identity(entry["project"], study)
        records, metadata = pd.DataFrame(entry["record"]), pd.DataFrame(entry["metadata"])
        raw_count = len(records)
        filtered, excluded = filter_source_records(records, metadata, pid)
        folded, folded_meta, mapping, folding = fold_verified_twins(filtered, metadata, pid)
        folded["project_id"] = pid
        folded["source_project"] = study["source_project"]
        folded["study_name"] = study["study_name"]
        folded["study_population"] = study["population"]
        folded["response_key"] = str(pid) + ":" + folded["record_id"].astype(str)
        folded["provenance_verified"] = True
        records_by_pid[pid] = folded.reset_index(drop=True)
        metadata_by_pid[pid], raw_metadata_by_pid[pid] = folded_meta, metadata
        exclusions.append(excluded)
        mappings.append(mapping)
        foldings.append(folding)
        provenance.append({"project_id": pid, "source_project": study["source_project"],
                           "study_name": study["study_name"], "api_project_title": entry["project"]["project_title"],
                           "population": study["population"], "reference_validity": study["reference_validity"],
                           "token_environment_variable": study["token_env"], "project_identity_verified": True,
                           "source_mode": "Fresh REDCap API export" if refresh else "Explicit offline snapshot; SHA-256 verified",
                           "fetched_at_utc": manifest["fetched_at_utc"], "snapshot_id": manifest["snapshot_id"],
                           "raw_records": raw_count, "excluded_records": len(excluded), "included_records": len(folded),
                           "archive_clones_excluded": int(excluded["archive_clone"].sum()),
                           "prelaunch_protocol_excluded": int(excluded["prelaunch_protocol"].sum()),
                           "test_word_excluded": int(excluded["contains_test_word"].sum()),
                           "source_mapping_review_records": int(folded["source_mapping_issue"].sum()),
                           "metadata_fields": len(metadata),
                           "record_sha256": manifest["files"][f"{pid}_record.json"]["sha256"],
                           "metadata_sha256": manifest["files"][f"{pid}_metadata.json"]["sha256"]})
    records = pd.concat(records_by_pid.values(), ignore_index=True, sort=False)
    if not records["response_key"].is_unique:
        raise ValueError("Duplicate study-qualified record keys survived source preparation.")
    return RefinedSources(records, records_by_pid, metadata_by_pid, raw_metadata_by_pid,
                          pd.DataFrame(provenance), pd.concat(exclusions, ignore_index=True),
                          pd.concat(mappings, ignore_index=True), pd.concat(foldings, ignore_index=True),
                          registry, snapshot_dir)


def load_refined_sources(project_dir: Path | str, refresh: bool = True) -> RefinedSources:
    """Load four independently identified studies, excluding tests before folding.

    ``refresh=False`` is an explicit offline request. It never accesses legacy
    data_cache exports, archived PID 4700 records, or partially refreshed files.
    """
    project_dir = Path(project_dir).resolve()
    registry = load_study_registry(project_dir)
    cache_root = project_dir / "Caregiver Outputs" / "restricted" / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_root.chmod(0o700)
    latest_path = cache_root / "refined_latest_snapshot.json"
    raw: dict[int, dict[str, Any]] = {}
    if refresh:
        config = yaml.safe_load((project_dir / "config.yaml").read_text())["redcap"]
        env_path = project_dir.parents[1] / ".env"
        if not env_path.is_file():
            raise FileNotFoundError("Repository .env is required for a live refined export.")
        secrets = dotenv_values(env_path)
        api_url = str(secrets.get(config["api_url_env"]) or "").strip()
        if not api_url.startswith("https://"):
            raise ValueError("The .env REDCap API URL must use HTTPS.")
        tokens = {pid: str(secrets.get(study["token_env"]) or "").strip() for pid, study in registry.items()}
        if any(not token for token in tokens.values()):
            missing = [study["token_env"] for pid, study in registry.items() if not tokens[pid]]
            raise ValueError("Missing configured token environment variables: " + ", ".join(missing))
        if len(set(tokens.values())) != len(tokens):
            raise ValueError("Two configured studies share the same token; refusing ambiguous project mapping.")
        fetched_at = datetime.now(timezone.utc)
        snapshot_id = fetched_at.strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_dir = cache_root / snapshot_id
        snapshot_dir.mkdir(mode=0o700)
        manifest = {"snapshot_id": snapshot_id, "fetched_at_utc": fetched_at.isoformat(),
                    "pids": list(registry), "files": {}, "reference_sha256": _digest(project_dir / REFERENCE_FILE)}
        timeout, retries = int(config.get("request_timeout_seconds", 120)), max(1, int(config.get("request_retries", 3)))
        # Validate ALL identities before any participant export.
        for pid, study in registry.items():
            project = _fetch(api_url, tokens[pid], pid, "project", timeout, retries)
            validate_project_identity(project, study)
            raw[pid] = {"project": project}
        for pid in registry:
            for content in ("metadata", "record", "instrument"):
                raw[pid][content] = _fetch(api_url, tokens[pid], pid, content, timeout, retries)
        for pid, content_data in raw.items():
            for content, value in content_data.items():
                path = snapshot_dir / f"{pid}_{content}.json"
                _private_json(path, value)
                manifest["files"][path.name] = {"sha256": _digest(path), "rows": 1 if content == "project" else len(value)}
        # The manifest is not published until source validation and filtering pass.
        result = _prepare_sources(raw, registry, snapshot_dir, manifest, refresh=True)
        for name, frame in [("provenance", result.provenance), ("exclusions", result.exclusions),
                            ("field_mapping", result.field_mapping), ("bilingual_folding", result.folding_audit)]:
            path = snapshot_dir / f"{name}.csv"
            frame.to_csv(path, index=False)
            path.chmod(0o600)
            manifest["files"][path.name] = {"sha256": _digest(path), "rows": len(frame)}
        _private_json(snapshot_dir / "manifest.json", manifest)
        # A unique temporary name also prevents simultaneous runs from sharing
        # an incomplete latest-pointer file.
        latest_temporary = cache_root / f".refined_latest_snapshot_{snapshot_id}.tmp"
        _private_json(latest_temporary, manifest)
        os.replace(latest_temporary, latest_path)
    else:
        if not latest_path.is_file():
            raise FileNotFoundError("No verified refined snapshot exists; run with refresh=True.")
        manifest = json.loads(latest_path.read_text())
        snapshot_id = manifest["snapshot_id"]
        if not re.fullmatch(r"\d{8}T\d{12}Z", snapshot_id):
            raise ValueError("Malformed refined snapshot identifier.")
        snapshot_dir = cache_root / snapshot_id
        if set(manifest["pids"]) != set(registry):
            raise ValueError("Cached snapshot and configured studies differ; refresh is required.")
        if manifest["reference_sha256"] != _digest(project_dir / REFERENCE_FILE):
            raise ValueError("Study reference changed since snapshot; refresh is required.")
        if json.loads((snapshot_dir / "manifest.json").read_text()) != manifest:
            raise ValueError("Snapshot manifest and latest verified pointer differ.")
        for filename, details in manifest["files"].items():
            if Path(filename).name != filename:
                raise ValueError("Unsafe filename in the source snapshot manifest.")
            path = snapshot_dir / filename
            if not path.is_file() or _digest(path) != details["sha256"]:
                raise ValueError(f"Missing or changed refined snapshot file: {filename}.")
        for pid in registry:
            raw[pid] = {}
            for content in ("project", "metadata", "record", "instrument"):
                filename = f"{pid}_{content}.json"
                path = snapshot_dir / filename
                if not path.is_file() or _digest(path) != manifest["files"][filename]["sha256"]:
                    raise ValueError(f"Missing or changed refined snapshot file: {filename}.")
                raw[pid][content] = json.loads(path.read_text())
        result = _prepare_sources(raw, registry, snapshot_dir, manifest, refresh=False)
    return result
