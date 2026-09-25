"""Source-backed field and candidate audit; candidates never alter production decisions.

Presentation tables contain only structured evidence and pseudonymous review IDs.
Original response text remains in the existing restricted, digest-verified snapshot.
Desk-review observations are not research-team adjudications or accuracy labels.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from refined_sources import choice_map
from refined_screening import COMPLETION_FIELDS, RULES, _rating_features

MISSING = {"", "nan", "none", "<na>", "null", "na", "n/a", "[not completed]"}
CANDIDATES = {
    "A": ("Standalone test keyword: context review", "Applicable metadata text/notes fields in both languages", "Existing standalone test exclusion retained; proposal is to distinguish substantive clinical-testing discussion from explicit test entries", "Review exclusion context; changes require Jessica's approval", "High: this survey explicitly asks about autism screening tests"),
    "B": ("Band-Aid attention response", "elig_knee and applicable language twin; metadata choice labels", "Answered options differ from Band-Aid alone; missing assessed separately; no-image/opt-out remains accessibility review", "Retain existing R13 review; do not add automatic exclusion", "Medium: select-all wording, image visibility and access needs"),
    "C": ("Pronoun and gender descriptions", "pronouns_elig; demo_gender", "Observe existing She/Man or He/Woman pairing only; no logically incompatible identity combination is assumed", "Context only; reject identity-only exclusion", "High: these self-descriptions can legitimately coexist"),
    "D": ("Disability and diagnosis interpretation", "fif_child_needs; fif_num_autistic; fif_needs_list", "Explicit No plus at least one autistic child; separately note condition words in stored needs narrative for desk review", "Retain R15 clarification; confirm qualifying conditions before expansion", "High: disability interpretation and stale hidden answers"),
    "E": ("Number of children consistency", "fif_num_children; fif_num_autistic; fif_childrens_ages; metadata-gated follow-ups", "Autistic lower bound exceeds total-child upper bound or age bands exceed upper bound; open-ended labels retain bounds", "Retain R8; review nonnumeric/unmapped count codes; no guessed numeric values", "Medium: age-band interpretation, stale branches, open-ended counts"),
    "F": ("Demographic consistency: independent age agreement", "age_confirm_elig; demo_momdob; eligibility_timestamp; zip_demo; dob_child1", "R9 concern OR both reported and date-derived age available and absolute age difference >1 year", "Candidate shadow manual review for age disagreement; never infer fraud", "Medium: reported age or birthdate could be a typo; dates may be entered retrospectively"),
    "G": ("Free-text quality beyond length", "Unvalidated participant narrative fields selected from metadata", "Existing substantive-text similarity OR exact placeholder token OR one character repeated at least six times", "Candidate shadow review; never reject short valid text by length alone", "Medium: technical strings or unusual valid brief responses"),
    "H": ("Cross-record duplication", "Normalized payment email; rating response fingerprint; narrative similarity; eligibility_timestamp", "Existing R5/R7 OR same nonempty normalized payment email on multiple records", "Existing duplicate-contact hold retained; new combinations await approval", "High: household shared email and identical legitimate opinions"),
    "I": ("Study-calibrated timing and impossible timestamps", "get_time_fif/val/tfa/demo; eligibility_timestamp; section timestamps", "Existing R1/R2/R3 OR negative section time OR malformed nonempty timestamp OR last section earlier than start", "Retain existing timing review; approve study-specific thresholds before recalibration", "High: inactive gaps and timer instrumentation are not respondent deception"),
}


def _s(values):
    return values.astype("string").fillna("").str.strip()


def _plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value)))).strip()


def pseudonym(pid, record_id):
    """Stable pseudonym, not cryptographic anonymization of guessable public IDs."""
    return "R-" + hashlib.sha256(f"{int(pid)}:{record_id}".encode()).hexdigest()[:12]


def _narrative_fields(records, metadata):
    return [r["field_name"] for r in metadata.to_dict("records")
            if r["field_name"] in records and r["field_type"] in {"text", "notes"}
            and not str(r.get("text_validation_type_or_show_slider_number", "")).strip()
            and re.search(r"comment|explain|feedback|reason|firstsigns_what|ethic_details|whylater|needs_list", r["field_name"])]


def candidate_signals(sources, baseline_scored):
    """Observed candidate flags on retained scoring cohort; no decision mutation.

    Includes Candidate A..I, <letter> evidence fields, age-disagreement and
    missing-eligibility detail columns. Root can join on Response key.
    """
    frames = []
    for pid, source in sources.records_by_pid.items():
        d = source.reset_index(drop=True)
        b = baseline_scored.loc[baseline_scored["REDCap PID"].eq(pid)].set_index("Record ID").loc[d["record_id"].astype(str)].reset_index()
        meta = sources.metadata_by_pid[pid].set_index("field_name").to_dict("index")
        z = pd.DataFrame({"Response key": b["Response key"], "Study PID": pid, "Study name": sources.registry[pid]["study_name"]})
        for code in CANDIDATES:
            z[f"Candidate {code}"] = False
            z[f"{code} evidence fields"] = ""
        for code, flags in {"B": ["R13"], "C": ["R14"], "D": ["R15"], "E": ["R8"], "F": ["R9"], "G": ["R7"], "H": ["R5", "R7"], "I": ["R1", "R2", "R3"]}.items():
            z[f"Candidate {code}"] = b[flags].eq(1).any(axis=1)
            z.loc[z[f"Candidate {code}"], f"{code} evidence fields"] = CANDIDATES[code][1]
        for f in [r["field_name"] for r in sources.metadata_by_pid[pid].to_dict("records") if r["field_type"] in {"text", "notes"} and r["field_name"] in d]:
            hit = _s(d[f]).str.contains(r"\btest\b", case=False, regex=True)
            z.loc[hit, "Candidate A"] = True
            z.loc[hit, "A evidence fields"] += f + "; "
        reported = pd.to_numeric(d.get("age_confirm_elig", pd.Series("", index=d.index)), errors="coerce")
        born = pd.to_datetime(d.get("demo_momdob", pd.Series("", index=d.index)), errors="coerce", format="mixed")
        start = pd.to_datetime(d.get("eligibility_timestamp", pd.Series("", index=d.index)), errors="coerce", format="mixed")
        birthday_pending = (start.dt.month < born.dt.month) | ((start.dt.month == born.dt.month) & (start.dt.day < born.dt.day))
        derived = (start.dt.year - born.dt.year - birthday_pending.astype(int)).where(start.notna() & born.notna())
        z["Age disagreement over one year"] = reported.notna() & derived.notna() & reported.sub(derived).abs().gt(1)
        z["Absolute age difference years"] = reported.sub(derived).abs()
        z.loc[z["Age disagreement over one year"], "Candidate F"] = True
        z.loc[z["Age disagreement over one year"], "F evidence fields"] = "age_confirm_elig; demo_momdob; eligibility_timestamp"
        z["New text quality concern"] = False
        for f in _narrative_fields(d, sources.metadata_by_pid[pid]):
            v = _s(d[f]).str.lower()
            hit = v.str.fullmatch(r"(?:test|testing|asdf+|qwerty+|placeholder|dummy|sample|xxx+|zzz+)") | v.str.fullmatch(r"(.)\1{5,}")
            z.loc[hit, "Candidate G"] = True
            z.loc[hit, "New text quality concern"] = True
            z.loc[hit, "G evidence fields"] += f + "; "
        duplicate = b["Responses sharing payment email"].gt(1)
        z.loc[duplicate, "Candidate H"] = True
        z.loc[duplicate, "H evidence fields"] += "; normalized payment email (value withheld)"
        timestamp_fields = [f for f in ["eligibility_timestamp"] + [f.replace("_complete", "_timestamp") for f in COMPLETION_FIELDS] if f in d]
        timestamps = pd.DataFrame({f: pd.to_datetime(d[f], errors="coerce", format="mixed") for f in timestamp_fields})
        malformed = pd.DataFrame({f: ~_s(d[f]).str.lower().isin(MISSING) & timestamps[f].isna() for f in timestamp_fields}).any(axis=1)
        end_fields = [f for f in timestamp_fields if f != "eligibility_timestamp"]
        end = timestamps[end_fields].max(axis=1) if end_fields else pd.Series(pd.NaT, index=d.index)
        z["Malformed timestamp"] = malformed
        z["Negative elapsed duration"] = start.notna() & end.notna() & end.lt(start)
        invalid_time = malformed | z["Negative elapsed duration"] | b["Timing data issue"].eq(1)
        z.loc[invalid_time, "Candidate I"] = True
        z.loc[invalid_time, "I evidence fields"] += "; malformed or impossible timing field"
        critical = []
        failures = []
        for f in ["age_elig", "children_elig", "fif_num_children", "fif_num_autistic", "fif_child_needs"]:
            choices = choice_map(meta.get(f, {}))
            v = _s(d.get(f, pd.Series("", index=d.index))).str.replace(r"\.0$", "", regex=True)
            critical.append(pd.Series(np.where(v.isin(choices), "", f), index=d.index))
            if f in {"age_elig", "children_elig"}:
                yes = {c for c, label in choices.items() if _plain(label).lower() in {"yes", "sí", "si"}}
                failures.append(v.isin(choices) & ~v.isin(yes))
        z["Missing or unmapped critical fields"] = pd.concat(critical, axis=1).apply(lambda r: "; ".join(v for v in r if v), axis=1)
        z["Explicit eligibility failure"] = pd.concat(failures, axis=1).any(axis=1)
        z["Candidate rules pending approval"] = z.apply(lambda r: "; ".join(c for c in ["F", "G", "I"] if (r["Age disagreement over one year"] if c == "F" else r["New text quality concern"] if c == "G" else r["Malformed timestamp"] or r["Negative elapsed duration"])), axis=1)
        frames.append(z)
    return pd.concat(frames, ignore_index=True)


def _raw_records(sources, pid):
    return pd.DataFrame(json.loads((sources.snapshot_dir / f"{pid}_record.json").read_text())).fillna("")


def _nonarchive_mask(records, pid):
    if int(pid) == 5749:
        return ~pd.to_numeric(records["record_id"], errors="coerce").between(1, 445)
    return pd.Series(True, index=records.index)


def _unmapped(records, metadata):
    findings = {}
    affected = pd.Series(False, index=records.index)
    for r in metadata.to_dict("records"):
        f = r["field_name"]
        choices = choice_map(r)
        if r["field_type"] == "checkbox":
            columns = [c for c in records if c.startswith(f + "___")]
            for c in columns:
                v = _s(records[c]).str.replace(r"\.0$", "", regex=True)
                hit = v.ne("") & ~v.isin(["0", "1"])
                if c.split("___", 1)[1] not in choices:
                    hit |= v.eq("1")
                if hit.any():
                    findings[c] = int(hit.sum()); affected |= hit
        elif choices and f in records:
            v = _s(records[f]).str.replace(r"\.0$", "", regex=True)
            hit = v.ne("") & ~v.isin(choices)
            if hit.any():
                findings[f] = int(hit.sum()); affected |= hit
    return findings, affected


def _field_rule_map(records, metadata):
    """Read source-supported dynamic scale/narrative sets, not all numeric fields."""
    meta = metadata.set_index("field_name").to_dict("index")
    fields = {}
    def add(names, rules):
        for name in names:
            fields.setdefault(name, set()).update(rules)
    add(["get_time_fif", "get_time_val", "get_time_tfa", "get_time_demo"], ["R1", "R3"])
    add(["get_time_tfa"], ["R2"])
    add(_rating_features(records, meta)[-1], ["R4", "R5"])
    add(["eligibility_timestamp"], ["R6", "R9", "R12"])
    add(_narrative_fields(records, metadata), ["R7"])
    add(["fif_num_children", "fif_num_autistic", "fif_childrens_ages"], ["R8"])
    for f, r in meta.items():
        logic = str(r.get("branching_logic", ""))
        if re.search(r'\[fif_num_autistic\]\s*(?:>\s*[\'\"]?\d+[\'\"]?|<>\s*[\'\"]?0[\'\"]?)', logic) and not re.search(r"\bor\b", logic, re.I) and r.get("field_type") not in {"descriptive", "calc"} and "@CALCTEXT" not in str(r.get("field_annotation", "")).upper():
            add([f], ["R8"])
    add(["age_confirm_elig", "demo_momdob", "demo_country", "zip_demo", "dob_child1"], ["R9"])
    add(["demo_email", "email_elig"], ["R10", "R11", "CONTACT"])
    add(["demo_email_confirm", "demo_email2"], ["CONTACT"])
    add(COMPLETION_FIELDS, ["COMPLETION", "R11"])
    add(["elig_knee"], ["R13"])
    add(["pronouns_elig", "demo_gender"], ["R14"])
    add(["fif_child_needs", "fif_num_autistic"], ["R15"])
    return fields


def input_audit(sources, baseline, signals, project_dir):
    rows = []
    for pid, settings in sources.registry.items():
        d = _raw_records(sources, pid)
        applicable = d.loc[_nonarchive_mask(d, pid)]
        m = sources.raw_metadata_by_pid[pid]
        invalid, invalid_rows = _unmapped(applicable, m)
        s = signals.loc[signals["Study PID"].eq(pid)]
        x = sources.exclusions.loc[sources.exclusions.project_id.eq(pid)]
        missing_status = pd.DataFrame({f: _s(applicable.get(f, pd.Series("", index=applicable.index))).eq("") for f in COMPLETION_FIELDS}).any(axis=1)
        exact_duplicates = int(d.duplicated().sum())
        duplicate_ids = int(_s(d.record_id).duplicated(keep=False).sum())
        empty = int(d.drop(columns=["record_id"], errors="ignore").apply(lambda c: _s(c).eq("")).all(axis=1).sum())
        rows.append({"File or dataset": f"Verified REDCap PID {pid}: {pid}_record.json / {pid}_metadata.json", "Study PID": pid,
          "Study name": settings["study_name"], "Number of records": len(d), "Record-ID field": "record_id (key = study PID + record_id)",
          "Completion-status field": "; ".join(COMPLETION_FIELDS), "Email field": "demo_email; email_elig fallback; confirmation when available",
          "Timing fields": "eligibility_timestamp; four section timestamps; get_time_fif/val/tfa/demo",
          "Current-classification field": "Reconstructed Recommended action from frozen refined_screening.py baseline",
          "Data dictionary available": f"Yes: {len(m)} live-verified fields", "Duplicate records": f"{exact_duplicates} full rows; {duplicate_ids} duplicate-ID rows",
          "Missing critical fields": f"{int(_s(d.record_id).eq('').sum())} missing IDs; {int(missing_status.sum())} applicable rows with missing required completion status; {int(s['Missing or unmapped critical fields'].ne('').sum())} scored rows with absent/invalid core eligibility responses",
          "Unmapped codes": f"{int(invalid_rows.sum())} applicable records; {len(invalid)} fields; " + ("; ".join(f"{f}: {n}" for f,n in invalid.items()) or "none"),
          "Study-specific exclusions": f"Archive 1–445: {int(x.archive_clone.sum())}; prelaunch protocol: {int(x.prelaunch_protocol.sum())}; standalone test: {int(x.contains_test_word.sum())}. Reasons overlap; unique excluded={len(x)}.",
          "Audit notes": f"One row/respondent; nonlongitudinal and nonrepeating identity verified. Raw={len(d)}, analysis after archive={len(applicable)}, baseline-scored={len(s)}. Empty non-ID rows={empty}; malformed timestamp rows={int(s['Malformed timestamp'].sum())}; negative elapsed rows={int(s['Negative elapsed duration'].sum())}. Missing states retained, not invented.",
          "Analysis records after archive exclusion": len(applicable), "Baseline-scored records": len(s)})
    for filename, purpose in [
        ("Survey Summary and Notes 07222026(Sheet1).csv", "Authoritative PID/study title and recruitment cohort reference; cohort designation is not adjudicated respondent validity"),
        ("config.yaml", "Expected project IDs and credential variable names; secrets are not included in this audit"),
        ("refined_sources.py; refined_screening.py; bot_analysis.ipynb", "Verified source cleaning, current executable rules and reproducible notebook entrypoint"),
        ("Caregiver Outputs/table_38_dirty4581_review_queue.csv", "Legacy review queue; inspect adjudication fields, never use old automated decisions as human labels"),
    ]:
        notes = purpose
        if filename.endswith("review_queue.csv"):
            path = project_dir / filename
            if path.exists():
                prior = pd.read_csv(path, dtype=str).fillna("")
                filled = int(_s(prior.get("adjudication_decision", pd.Series("", index=prior.index))).ne("").sum())
                notes += f"; rows={len(prior)}; nonblank adjudication_decision={filled}."
        rows.append({"File or dataset": filename, "Study PID": "All four studies", "Study name": "All four studies", "Number of records": "Not applicable", "Record-ID field": "Not applicable", "Completion-status field": "Not applicable", "Email field": "Not applicable", "Timing fields": "Not applicable", "Current-classification field": "Not applicable", "Data dictionary available": "Not applicable", "Duplicate records": "Not applicable", "Missing critical fields": "Not applicable", "Unmapped codes": "Not applicable", "Study-specific exclusions": "Not applicable", "Audit notes": notes})
    rows.append({"File or dataset": "TOTAL respondent data (support documents excluded from total)", "Study PID": "All four studies", "Study name": "All four studies", "Number of records": sum(len(_raw_records(sources,p)) for p in sources.registry), "Analysis records after archive exclusion": sum(len(_raw_records(sources,p).loc[_nonarchive_mask(_raw_records(sources,p),p)]) for p in sources.registry), "Baseline-scored records": len(baseline), "Audit notes": "Total is distinct PID/record keys. Archive excluded once; overlapping policy reasons never added as unique people."})
    return pd.DataFrame(rows).fillna("Not applicable")


def variable_inventory(sources):
    rows = []
    for pid, metadata in sources.raw_metadata_by_pid.items():
        raw = _raw_records(sources, pid)
        raw = raw.loc[_nonarchive_mask(raw, pid)]
        canonical = sources.metadata_by_pid[pid]
        use = _field_rule_map(sources.records_by_pid[pid], canonical)
        invalid, _ = _unmapped(raw, metadata)
        known = set()
        for r in metadata.to_dict("records"):
            f = r["field_name"]; known.add(f)
            base = re.sub(r"_(?:s|sp)$", "", f) if pid == 5749 else f
            rule_ids = sorted(use.get(base, set()))
            if r["field_type"] in {"text", "notes"}:
                rule_ids.append("TEST_WORD")
            columns = [c for c in raw if c.startswith(f + "___")] if r["field_type"] == "checkbox" else [f] if f in raw else []
            missing = int(raw[columns].apply(lambda c: _s(c).str.lower().isin(MISSING)).all(axis=1).sum()) if columns else len(raw)
            if r["field_type"] == "checkbox" and columns:
                missing = int((~raw[columns].apply(lambda c: _s(c).eq("1")).any(axis=1)).sum())
            err = sum(v for k,v in invalid.items() if k == f or k.startswith(f+"___"))
            numeric_issue = 0
            validation = str(r.get("text_validation_type_or_show_slider_number", ""))
            if columns == [f] and (r["field_type"] in {"slider", "calc"} or validation.startswith(("int", "number"))):
                v = _s(raw[f]); numeric_issue = int((v.ne("") & pd.to_numeric(v, errors="coerce").isna()).sum())
            candidate = []
            for code, info in CANDIDATES.items():
                if base in info[1] or (code == "G" and base in _narrative_fields(sources.records_by_pid[pid], canonical)):
                    candidate.append(code)
            rows.append({"Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Variable name": f,
              "Variable label": _plain(r.get("field_label", "")) or "No label supplied in REDCap metadata",
              "Data type": r["field_type"] + (f"; validation={validation}" if validation else ""),
              "Response options": "; ".join(f"{k} = {_plain(v)}" for k,v in choice_map(r).items()) or "Not applicable / no enumerated choices",
              "Missing-value representation": f"Empty string/NA retained; {missing}/{len(raw)} applicable raw records blank or no selected checkbox; hidden branches may legitimately be blank",
              "Used by current bot": "Yes" if rule_ids else "No", "Existing rule IDs": "; ".join(rule_ids) or "None",
              "Candidate use": "; ".join(candidate) or "No new use proposed", "Normalization needed": "Trim whitespace; canonical missing states; metadata choice labels; numeric parse if declared; verified language-code mapping for PID 5749",
              "Data-quality issue": f"Unmapped category cells={err}; nonnumeric declared numeric cells={numeric_issue}" + ("; descriptive/nonresponse field" if r["field_type"] == "descriptive" else ""),
              "Recommendation": "Preserve raw value and branching context; do not infer missing answers" + ("; investigate observed code/type issue" if err or numeric_issue else "")})
        for f in raw:
            if f in known or "___" in f:
                continue
            rules = ["COMPLETION"] if f in COMPLETION_FIELDS else ["R6", "R12"] if f == "eligibility_timestamp" else ["TIMING_AUDIT"] if f.endswith("_timestamp") else []
            rows.append({"Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Variable name": f, "Variable label": "REDCap export system field", "Data type": "System field (raw export string)", "Response options": "0 = incomplete; 1 = unverified; 2 = complete" if f.endswith("_complete") else "Not applicable", "Missing-value representation": "Empty string retained", "Used by current bot": "Yes" if rules else "No", "Existing rule IDs": "; ".join(rules) or "None", "Candidate use": "I" if f.endswith("_timestamp") else "No new use proposed", "Normalization needed": "Parse timestamp or completion code as applicable", "Data-quality issue": "System field absent from dictionary by design", "Recommendation": "Keep as provenance / timing / completion context"})
    return pd.DataFrame(rows)


def candidate_rule_table(sources, baseline, signals):
    rows = []
    for pid in sources.registry:
        s = signals.loc[signals["Study PID"].eq(pid)]
        b = baseline.loc[baseline["REDCap PID"].eq(pid)]
        x = sources.exclusions.loc[sources.exclusions.project_id.eq(pid) & ~sources.exclusions.archive_clone.astype(bool)]
        for code, (name, fields, condition, action, risk) in CANDIDATES.items():
            mask = s[f"Candidate {code}"]
            affected = int(mask.sum())
            if code == "A":
                affected += int(x.contains_test_word.sum())
            example = "No observed trigger in this study"
            if mask.any():
                key = s.loc[mask, "Response key"].iloc[0]
                example = pseudonym(pid, key.split(":", 1)[1]) + ": " + s.loc[mask, f"{code} evidence fields"].iloc[0]
            elif code == "A" and x.contains_test_word.any():
                hit = x.loc[x.contains_test_word.astype(bool)].iloc[0]
                example = pseudonym(pid, hit.record_id) + ": standalone word in " + str(hit.matched_text_fields)
            negative = s.loc[~mask]
            nontrigger = (pseudonym(pid, negative.iloc[0]["Response key"].split(":",1)[1]) + ": specified observed condition absent; not proof of validity") if len(negative) else "No observed non-trigger"
            if code == "A":
                status = "Existing literal exclusion authorized by user; contextual relaxation requires clarification"
            elif code == "C":
                status = "Rejected as identity-only exclusion; current context-only observation retained"
            else:
                status = "Existing component retained; new extension pending Jessica's approval"
            rows.append({"Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Candidate rule ID": code,
              "Rule name": name, "Business rationale": "Make the observed concern reviewable without treating unadjudicated response patterns as proven fraud",
              "Variables used": fields, "Proposed logical condition": condition, "Normalization": "Casefold/trim; metadata labels; preserve missing states; numeric/date parse where appropriate",
              "Missing-data handling": "Not a positive contradiction; missing required eligibility produces a separate payment hold; unavailable candidate inputs are not imputed",
              "Proposed severity": "Context only" if code == "C" else "Manual review / clarification",
              "Proposed action": action, "Number potentially affected": affected, "Example trigger": example,
              "Example non-trigger": nontrigger, "False-positive risk": risk,
              "Requires clarification": "Yes: Jessica must approve any new production condition, threshold or exclusion; no approval inferred from this analysis",
              "Approval status": status, "Observed scoring denominator": len(s),
              "Current Pay Now with observed candidate": int(mask.to_numpy().dot(b["Recommended action"].eq("Pay now").to_numpy().astype(int))),
              "Interpretation": "Potentially affected includes overlapping existing rules and is not an incremental change count. Candidate A includes nonarchive policy-excluded records."})
    return pd.DataFrame(rows)


def manual_review_table(sources, baseline, signals):
    """Reproducible stratified analyst evidence review, not fabricated adjudication.

    Selection covers all observed age disagreements and two records per other
    requested stratum/study, plus deterministic random comparison records.
    Full original response patterns remain linked by the private source key.
    """
    joined = baseline.merge(signals, on="Response key", suffixes=("", " candidate"), validate="one_to_one")
    selected = {}
    def add(frame, label, n=2):
        for idx in frame.head(n).index:
            selected.setdefault(idx, set()).add(label)
    for pid in sources.registry:
        d = joined.loc[joined["REDCap PID"].eq(pid)]
        add(d[d["Age disagreement over one year"]], "Age disagreement; newly missed logic", len(d))
        add(d[d["Recommended action"].eq("Pay now")], "Current Pay Now")
        add(d[d["Recommended action"].eq("Do not pay")], "Current refusal recommendation; invalidity unconfirmed")
        add(d[d["R8"].eq(1) | d["R15"].eq(1)], "Conflicting response fields")
        add(d[d["Candidate G"]], "Substantive repeated or unusual free text")
        add(d[d["Candidate I"]], "Unusual timing")
        add(d[d["Candidate H"]], "Duplicate indicator")
        add(d[d["Recommended action"].eq("Check by hand") & d["Independent evidence families"].le(1)], "Borderline review")
        add(d.sample(n=min(2,len(d)), random_state=20260925), "Seeded random comparison")
        add(d[d["Source mapping issue"].eq(1)], "Study-specific mapping issue", len(d))
    rows = []
    for idx, strata in sorted(selected.items()):
        r = joined.loc[idx]
        pid = int(r["REDCap PID"])
        source = sources.records_by_pid[pid]
        raw = source.loc[source.record_id.astype(str).eq(str(r["Record ID"]))].iloc[0]
        meta = sources.metadata_by_pid[pid].set_index("field_name").to_dict("index")
        statements = []
        for f in ["age_elig", "children_elig", "fif_num_children", "fif_num_autistic", "fif_child_needs"]:
            statements.append(f + "=" + _plain(choice_map(meta.get(f, {})).get(str(raw.get(f, "")), "Missing/unmapped")))
        statements.extend(["four sections completed=" + r["Survey finished"], "usable payment contact=" + r["Email usable"],
                           "rating coverage=" + f"{r['Rating fraction answered']:.1%}",
                           "shared email records=" + str(int(r["Responses sharing payment email"])),
                           "timing evidence=" + str(int(r["Timing evidence"])), "independent evidence families=" + str(int(r["Independent evidence families"]))])
        if r["Age disagreement over one year"]:
            statements.append("reported versus date-derived age differs by " + str(int(r["Absolute age difference years"])) + " years; exact birthdate withheld")
        narrative = [f for f in _narrative_fields(source, sources.metadata_by_pid[pid]) if str(raw.get(f, "")).strip()]
        statements.append(f"{len(narrative)} nonempty narrative fields inspected through structured quality/similarity indicators; original text retained only in restricted source")
        evidence_fields = sorted({v.strip() for code in CANDIDATES if r[f"Candidate {code}"] for v in str(r[f"{code} evidence fields"]).split(";") if v.strip()})
        expected = "No independent validity label; current recommendation not overturned"
        missed = "No additional concrete contradiction demonstrated in inspected structured evidence"
        action = "Apply existing recommendation; Jessica adjudicates any unresolved concern"
        if r["Age disagreement over one year"]:
            expected = "HOLD FOR MANUAL REVIEW if candidate F is approved; validity not adjudicated"
            missed = "R9 chooses reported age when available and does not compare it with date-derived age"
            action = "Ask Jessica to approve an age-agreement review signal and clarify possible data-entry correction"
        elif r["Source mapping issue"]:
            expected = "HOLD FOR MANUAL REVIEW until metadata meaning is verified"
            missed = "No missed source guard: existing mapping hold triggers"
            action = "Research team resolves the bilingual option-label ambiguity using source instrument"
        elif r["Recommended action"] == "Do not pay":
            expected = "HOLD FOR MANUAL REVIEW; multiple weak families do not establish confirmed invalidity"
            missed = "Existing family-count refusal exceeds verified record-level truth"
            action = "Retain payment hold and obtain independent adjudication; avoid confirmed-bot claim"
        elif r["R14"] and r["Recommended action"] == "Pay now":
            missed = "Pronoun/gender pairing intentionally has no payment effect; legitimate identity protection"
        rows.append({"Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Anonymized record ID": pseudonym(pid,r["Record ID"]),
          "Current classification": r["Recommended action"], "Expected classification": expected,
          "Existing rules triggered": r["Which checks were flagged"] or "None", "Suspicious or contradictory fields": "; ".join(evidence_fields) or "None demonstrated",
          "Anonymized evidence": "; ".join(statements), "Missed logic": missed, "Proposed action": action,
          "Reviewer confidence": "High in arithmetic/source observation; respondent validity unadjudicated",
          "Manual-review notes": "Analyst desk review of observed structured response pattern; not Jessica approval or independent reference label. Strata: " + "; ".join(sorted(strata)),
          "Candidate rules pending approval": r["Candidate rules pending approval"] or "None newly triggered",
          "Review provenance": "Reproducible stratified evidence review; no manually supplied record-level validity label"})
    # Include actual excluded entries in the requested invalid / test-word stratum.
    for pid in sources.registry:
        x = sources.exclusions.loc[sources.exclusions.project_id.eq(pid) & ~sources.exclusions.archive_clone.astype(bool) & sources.exclusions.contains_test_word.astype(bool)].head(2)
        for r in x.itertuples():
            rows.append({"Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Anonymized record ID": pseudonym(pid,r.record_id), "Current classification": "EXCLUDE", "Expected classification": "Existing authorized exclusion; substantive context still needs research adjudication", "Existing rules triggered": "TEST_WORD", "Suspicious or contradictory fields": r.matched_text_fields, "Anonymized evidence": "Standalone test word found in original text; response text withheld from presentation. This does not establish a test/pilot respondent.", "Missed logic": "Literal matching does not distinguish clinical discussion from test entries", "Proposed action": "Jessica reviews contextual exception before changing the existing authorized filter", "Reviewer confidence": "High in keyword match; validity unadjudicated", "Manual-review notes": "Policy-excluded stratum; original text is in the existing restricted snapshot", "Candidate rules pending approval": "A", "Review provenance": "Source-backed desk review; research adjudication pending"})
    return pd.DataFrame(rows)


def root_cause_table(sources, baseline, signals):
    rows = []
    for pid in sources.registry:
        b = baseline.loc[baseline["REDCap PID"].eq(pid)].reset_index(drop=True)
        s = signals.loc[signals["Study PID"].eq(pid)].reset_index(drop=True)
        x = sources.exclusions.loc[sources.exclusions.project_id.eq(pid) & ~sources.exclusions.archive_clone.astype(bool)]
        cases = [
          ("Potential false negative: age disagreement in Pay Now; unadjudicated", int((s["Age disagreement over one year"] & b["Recommended action"].eq("Pay now")).sum()), "Reported age takes precedence over birthdate-derived age; record may be paid", "If approved, hold for clarification; no confirmed-invalidity claim", "Missing independent age-agreement comparison", "R9; F", "High", "Add shadow age-gap review, seek approval", "Candidate only; production unchanged"),
          ("Potential false positive: literal test-word exclusion; validity unknown", int(x.contains_test_word.sum()), "Exclude any standalone test word, including substantive screening discussion", "Preserve current explicit instruction until contextual exception approved", "Policy scope broader than test/pilot respondent intent", "TEST_WORD; A", "High", "Research-team adjudication of clinical context and approved revised exclusion scope", "Existing instruction preserved; exception pending"),
          ("Unresolved invalidity: multi-family refusal", int(b["Recommended action"].eq("Do not pay").sum()), "Do not pay based on at least three independent evidence families", "Payment hold for human adjudication; not a confirmed bot label", "Unvalidated combination action lacks independent reference labels", "R1; R2; R5; R7; R8; R13", "High", "Use explicit manual hold pending adjudication", "Classification clarification; no invented validity labels"),
          ("Missing-value handling: unavailable core eligibility", int(s["Missing or unmapped critical fields"].ne("").sum()), "Current score does not directly gate age_elig/children_elig; many missing cases are incomplete", "Required eligibility responses must be verified before payment", "Missing direct eligibility completeness gate", "ELIGIBILITY; COMPLETION", "High", "Explicitly gate required, metadata-valid eligibility information", "Safeguard specified; no observed current Pay Now miss in these fields"),
          ("Variable mapping: bilingual option ambiguity", int(b["Source mapping issue"].sum()), "Existing hold prevents interpretation of ambiguous bilingual label", "Keep payment held until source meaning verified", "Spanish label mismatch, not a respondent inconsistency", "SOURCE_MAPPING", "High", "Jessica confirms intended survey option; preserve original answers", "Detected and held; source instrument clarification pending"),
          ("Ambiguous wording: disability/autism", int(b.R15.sum()), "Explicit No plus autistic-child count triggers review", "Clarify disability interpretation, not automatic fraud exclusion", "Different interpretations of disability and needs", "R15; D", "Medium", "Approve condition taxonomy and review instructions", "Existing conservative review retained"),
          ("Study-specific coverage: attention question unavailable", len(b) if not b["Attention check available"].any() else 0, "R13 cannot evaluate a field absent from the study", "Mark not applicable; do not impute success or failure", "PID 4581 lacks Band-Aid question", "R13; B", "Medium", "Keep study-specific applicability explicit", "Absent-field guard implemented"),
          ("Timing instrumentation: malformed or reversed timestamps", int((s["Malformed timestamp"] | s["Negative elapsed duration"]).sum()), "Malformed timestamps coerce to missing; reversed elapsed durations are masked", "Review data capture if observed; never silently interpret as a valid duration", "Missing separate timestamp-quality reporting", "TIMING_AUDIT; I", "Medium", "Expose timestamp quality and source errors separately", "Shadow audit; new action pending approval"),
          ("Legitimate unusual response protection: pronouns/gender", int(b.R14.sum()), "Context-only observation; not a payment block", "Retain identity-only nonexclusion", "Uncommon identity combinations are not contradictions proving invalidity", "R14; C", "High if misused", "Reject identity-only fraud rule", "Conservative protection retained"),
        ]
        for category,n,current,expected,cause,rules,risk,fix,status in cases:
            rows.append({"Error category": category, "Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Number of affected records": n, "Current behavior": current, "Expected behavior": expected, "Root cause": cause, "Related rule IDs": rules, "Risk level": risk, "Recommended correction": fix, "Correction status": status})
    rows.append({"Error category": "Confirmed false-positive and false-negative counts", "Study PID": "All four studies", "Study name": "All four studies", "Number of affected records": "Not estimable", "Current behavior": "No independently adjudicated respondent-level validity labels supplied", "Expected behavior": "Estimate error rates only after reference adjudication", "Root cause": "Legacy reviewer-decision cells empty; study-cohort labels are not respondent truth", "Related rule IDs": "All implemented rules", "Risk level": "High", "Recommended correction": "Research team adjudicates sampled triggers and nontriggers independently", "Correction status": "Cannot adjudicate; no fabricated accuracy metrics"})
    return pd.DataFrame(rows)


def unresolved_questions(sources, baseline, signals):
    rows = []
    for pid in sources.registry:
        b = baseline.loc[baseline["REDCap PID"].eq(pid)].reset_index(drop=True)
        s = signals.loc[signals["Study PID"].eq(pid)].reset_index(drop=True)
        x = sources.exclusions.loc[sources.exclusions.project_id.eq(pid) & ~sources.exclusions.archive_clone.astype(bool)]
        questions = [
          ("Q-A", "A / TEST_WORD", "Should clinically relevant uses of test remain excluded?", int(x.contains_test_word.sum()), "Retain literal rule; or approve contextual exception with restricted-text adjudication", "Approve explicit contextual scope after reviewing real excluded responses", "Genuine caregivers discussing the survey subject may remain excluded"),
          ("Q-F", "F / R9", "Approve manual-review flag for reported age versus birthdate-derived age differing by more than one year?", int(s["Age disagreement over one year"].sum()), "Approve shadow condition as review only; adjust tolerance; defer", "Review only; first inspect the two current Pay Now records in PID 4797", "Unresolved age discrepancy can remain automatic-pay eligible under current rules"),
          ("Q-D", "D / R15", "Which diagnoses qualify, and does an explicit No conflict with autism or reflect wording interpretation?", int(b.R15.sum()), "Approve condition taxonomy plus clarification; maintain review; automatic exclusion not recommended", "Keep review only, clarify disability wording", "Valid interpretations could be misclassified as deception"),
          ("Q-C", "C / R14", "Confirm rejection of pronoun/gender-only exclusion", int(b.R14.sum()), "Context-only retention; remove context flag; identity-only exclusion not recommended", "Retain nonexclusion; no assumed incompatible identity combinations", "Identity discrimination and false-positive exclusion if misused"),
          ("Q-B", "B / R13", "Does select-all Band-Aid wording require exactly one choice, and how should no-image/opt-out be handled?", int(b.R13.sum()), "Existing review; approve revised expected combination; accommodate image failures", "Keep review only and retain separate missing/accessibility states", "Attention-check interpretation may be overly strict"),
          ("Q-H", "H / CONTACT / R5 / R7", "What independent evidence can establish duplicate participation instead of household sharing?", int(s["Candidate H"].sum()), "Review composite matches; permit household sharing after review; approve explicit confirmed-duplicate protocol", "Manual duplicate-payment review; no fraud inference from shared email alone", "Valid households may be excluded or duplicate payment unresolved"),
          ("Q-I", "I / R1 / R2 / R3", "Approve study-specific timing thresholds using adjudicated valid respondents and instrument length?", int(s["Candidate I"].sum()), "Retain current floors as review; recalibrate by study; no universal automatic exclusion", "Retain review only until calibrated", "Fast valid caregivers and language/instrument differences may inflate flags"),
          ("Q-M", "SOURCE_MAPPING", "Confirm intended meaning of the ambiguous Spanish child-health choice", int(b["Source mapping issue"].sum()), "Confirm source instrument label; review record; do not silently recode", "Keep mapping hold until Jessica verifies the meaning", "Incorrect diagnosis mapping could affect payment and scientific inference"),
          ("Q-L", "All rules", "Supply independent record-level adjudications for validity and reviewer identity", len(b), "Adjudicate stratified triggers/nontriggers; do not use cohort label as truth", "Create research-team decisions in existing local review output; no contact workflow", "Precision, recall and true false-positive/negative counts remain not estimable"),
        ]
        for q, rule, decision, n, options, recommended, risk in questions:
            rows.append({"Question ID": f"{q}-{pid}", "Study PID": pid, "Study name": sources.registry[pid]["study_name"], "Related rule": rule, "Decision needed": decision, "Why the decision matters": "Separates an observed source pattern from an approved payment criterion", "Number of affected records": n, "Available options": options, "Recommended option": recommended, "Risk if unresolved": risk, "Final decision": "Pending Jessica/research-team decision; no approval supplied", "Current Pay Now with age disagreement": int((s["Age disagreement over one year"] & b["Recommended action"].eq("Pay now")).sum()) if q == "Q-F" else "Not applicable"})
    return pd.DataFrame(rows)


def build_validity_audit_tables(sources, baseline_scored, revised_results=None, project_dir=None):
    """Build required Tables 1, 2, 4, 5, 15, 16 from the verified source snapshot."""
    project_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parent
    signals = candidate_signals(sources, baseline_scored)
    return {
        1: input_audit(sources, baseline_scored, signals, project_dir),
        2: variable_inventory(sources),
        4: manual_review_table(sources, baseline_scored, signals),
        5: candidate_rule_table(sources, baseline_scored, signals),
        15: root_cause_table(sources, baseline_scored, signals),
        16: unresolved_questions(sources, baseline_scored, signals),
    }
