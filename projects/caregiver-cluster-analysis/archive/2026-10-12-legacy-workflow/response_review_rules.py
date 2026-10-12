"""Review actual survey responses and produce a combined existing + proposed rule list.

This script loads the scored data from the refined pipeline, samples real
participant responses from key groups, examines answer patterns, and produces
the list Jessica requested:

    Item  |  Field Name  |  Flag

The list includes both existing implemented rules (R1-R15) and newly proposed
rules based on patterns observed in the actual data.

Usage:
    python response_review_rules.py          # loads from cached snapshot
    python response_review_rules.py --refresh  # pulls fresh from REDCap
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from refined_sources import load_refined_sources, choice_map, _plain_label
from refined_screening import (
    RULES, TOTAL_TIME_FLOOR_MIN, TFA_TIME_FLOOR_MIN, SECTION_FLOORS_MIN,
    DISPOSABLE_DOMAINS, _s, _n, _code, _choices, _match_code, score_refined_records,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_email(email: str) -> str:
    """Mask an email for display: a***z@domain.com."""
    if not email or "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def _short(text: str, maxlen: int = 80) -> str:
    text = str(text).strip()
    if len(text) > maxlen:
        return text[:maxlen - 3] + "..."
    return text


# ---------------------------------------------------------------------------
# Part 1: Existing rule inventory
# ---------------------------------------------------------------------------

def build_existing_rule_table() -> pd.DataFrame:
    """Build a table of all 15 implemented screening rules."""
    rows = []
    for code, rule in RULES.items():
        rows.append({
            "Rule ID": code,
            "Status": "Implemented",
            "Item / Check": rule["label"],
            "Field Name(s)": _rule_fields(code),
            "Flag / Condition": _rule_description(code),
            "Evidence Family": rule["family"],
            "Points": rule["points"],
        })
    return pd.DataFrame(rows)


def _rule_fields(code: str) -> str:
    """Map each rule to the field names it inspects."""
    field_map = {
        "R1": "get_time_fif, get_time_val, get_time_tfa, get_time_demo",
        "R2": "get_time_tfa",
        "R3": "get_time_fif, get_time_val, get_time_tfa, get_time_demo",
        "R4": "All rating-scale items in values + tfa forms",
        "R5": "All rating-scale items (SHA-256 fingerprint)",
        "R6": "eligibility_timestamp",
        "R7": "Free-text fields: comment, explain, feedback, reason, firstsigns_what, etc.",
        "R8": "fif_num_autistic, fif_num_children, fif_childrens_ages, autism follow-up fields",
        "R9": "age_confirm_elig, demo_momdob, demo_country, zip_demo, dob_child1",
        "R10": "demo_email, email_elig",
        "R11": "demo_email, email_elig, completion status fields",
        "R12": "eligibility_timestamp",
        "R13": "elig_knee (checkbox: Band-Aid attention check)",
        "R14": "pronouns_elig, demo_gender",
        "R15": "fif_child_needs, fif_num_autistic",
    }
    return field_map.get(code, "")


def _rule_description(code: str) -> str:
    """Concise plain-language description of each rule's trigger condition."""
    desc_map = {
        "R1": f"Total survey duration (sum of 4 section times) < {TOTAL_TIME_FLOOR_MIN} min",
        "R2": f"Thoughts & Feelings section time < {TFA_TIME_FLOOR_MIN} min",
        "R3": "Any individual section below its time floor (FIF<2.55, Val<0.22, TFA<6.67, Demo<0.95 min)",
        "R4": "Rating block with >=4 items answered, >=80% coverage, and SD < 0.15 (straight-lining)",
        "R5": "Exact SHA-256 fingerprint match on >=20 rating items with another record in same study",
        "R6": ">=2 other records started within ±120 seconds in the same study (sign-up burst)",
        "R7": "TF-IDF cosine similarity > 0.90 on substantive narrative (>=100 chars, >=20 words)",
        "R8": "Family count contradictions: 0 autistic children with gated follow-up answered, age bands > child count, autistic count > total children",
        "R9": "Age outside 18-100, malformed US ZIP, or parent-birth-year interval outside 10-60",
        "R10": "Email domain on disposable/temporary domain list",
        "R11": "Survey fully complete but no email address provided",
        "R12": "Survey started between midnight and 5am (REDCap local time)",
        "R13": "Band-Aid attention check: did not select 'Puts on a Band-Aid' or also selected other options",
        "R14": "Pronoun and gender mismatch: She/Her + Man, or He/Him + Woman",
        "R15": "Said 'No' to child with disability but reported >=1 autistic child",
    }
    return desc_map.get(code, RULES[code]["description"])


# ---------------------------------------------------------------------------
# Part 2: Examine actual responses to identify new patterns
# ---------------------------------------------------------------------------

def examine_responses(sources, scored: pd.DataFrame) -> dict:
    """Examine actual participant responses and identify suspicious patterns.

    Returns a dict with:
    - sample_reviews: list of per-record review dicts
    - pattern_findings: list of identified patterns/proposed rules
    - statistics: summary stats
    """
    findings = []
    sample_reviews = []
    stats = {}

    # --- Focus on records classified as "Pay now" ---
    pay_now = scored[scored["Recommended action"] == "Pay now"].copy()
    check = scored[scored["Recommended action"] == "Check by hand"].copy()
    do_not_pay = scored[scored["Recommended action"] == "Do not pay"].copy()

    stats["total_analyzed"] = len(scored)
    stats["pay_now"] = len(pay_now)
    stats["check_by_hand"] = len(check)
    stats["do_not_pay"] = len(do_not_pay)
    stats["incomplete"] = len(scored[scored["Recommended action"] == "Incomplete - not eligible"])

    # --- FINDING A: Band-Aid / elig_knee check (already R13 but let's see HOW it fires) ---
    attention_data = _analyze_attention_check(scored, sources)
    findings.append(attention_data)

    # --- FINDING B: Pronoun / Gender mismatch details ---
    pronoun_data = _analyze_pronoun_gender(scored, sources)
    findings.append(pronoun_data)

    # --- FINDING C: Disability contradiction details ---
    disability_data = _analyze_disability_contradiction(scored)
    findings.append(disability_data)

    # --- FINDING D: Free-text quality analysis ---
    freetext_data = _analyze_free_text(scored, sources)
    findings.append(freetext_data)

    # --- FINDING E: Number of children consistency ---
    children_data = _analyze_children_consistency(scored)
    findings.append(children_data)

    # --- FINDING F: Demographic plausibility ---
    demo_data = _analyze_demographics(scored)
    findings.append(demo_data)

    # --- FINDING G: Timing analysis ---
    timing_data = _analyze_timing(scored)
    findings.append(timing_data)

    # --- FINDING H: Duplicate email patterns ---
    email_data = _analyze_email_patterns(scored)
    findings.append(email_data)

    # --- FINDING I: Cross-record response duplication ---
    duplication_data = _analyze_cross_record_duplication(scored)
    findings.append(duplication_data)

    # --- Sample some "Pay now" records for manual review ---
    sample_reviews = _sample_pay_now_records(scored, sources)

    return {
        "sample_reviews": sample_reviews,
        "pattern_findings": findings,
        "statistics": stats,
    }


def _analyze_attention_check(scored: pd.DataFrame, sources) -> dict:
    """Analyze Band-Aid attention check responses among Pay Now records."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Records that passed the bot but have Band-Aid issues
    bandaid_flagged = scored[scored["R13"] == 1]
    bandaid_pay_now = pay_now[pay_now["R13"] == 1]

    # Check the actual selections more carefully
    band_selected = scored["Band-Aid selected"].sum()
    other_selected = scored.loc[scored["Other attention options selected"] > 0]
    answered = scored[scored["Attention check answered"] == 1]

    # Among Pay Now, how many selected Band-Aid alone vs with other options?
    pn_band_only = pay_now[
        (pay_now["Band-Aid selected"] == 1) & (pay_now["Other attention options selected"] == 0)
    ]
    pn_band_plus = pay_now[
        (pay_now["Band-Aid selected"] == 1) & (pay_now["Other attention options selected"] > 0)
    ]
    pn_no_band = pay_now[
        (pay_now["Band-Aid selected"] == 0) & (pay_now["Attention check answered"] == 1)
    ]

    return {
        "rule_id": "R13 (existing) + Candidate A",
        "item": "Band-Aid attention check (elig_knee)",
        "field": "elig_knee",
        "finding": (
            f"Total flagged by R13: {len(bandaid_flagged)}. "
            f"Among answered records: {len(answered)} answered, "
            f"{int(band_selected)} selected Band-Aid. "
            f"Pay Now: {len(pn_band_only)} selected Band-Aid only, "
            f"{len(pn_band_plus)} selected Band-Aid + other options, "
            f"{len(pn_no_band)} answered but did NOT select Band-Aid."
        ),
        "proposed_refinement": (
            "Consider: not just checking 'Puts on a Band-Aid' but also whether "
            "they checked it IN ADDITION to other responses. Checking multiple "
            "options on this question may indicate inattentive responding."
        ),
    }


def _analyze_pronoun_gender(scored: pd.DataFrame, sources) -> dict:
    """Analyze pronoun/gender mismatches in actual responses."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # She+Man or He+Woman mismatches
    she_man = scored[scored["She and man pairing"] == 1]
    he_woman = scored[scored["He and woman pairing"] == 1]
    mismatch_pay = pay_now[(pay_now["She and man pairing"] == 1) | (pay_now["He and woman pairing"] == 1)]

    # Get the actual pronoun/gender distributions
    pronoun_dist = scored["Pronoun response"].value_counts()
    gender_dist = scored["Gender response"].value_counts()

    return {
        "rule_id": "R14 (existing) + Candidate C",
        "item": "Pronoun and Gender consistency",
        "field": "pronouns_elig, demo_gender",
        "finding": (
            f"She/Her + Man: {len(she_man)} records. "
            f"He/Him + Woman: {len(he_woman)} records. "
            f"Of these, {len(mismatch_pay)} currently classified as 'Pay now'. "
            f"Pronoun distribution: {dict(pronoun_dist.head(5))}. "
            f"Gender distribution: {dict(gender_dist.head(5))}."
        ),
        "proposed_refinement": (
            "Mismatched pronoun and gender responses (e.g., selecting 'she' and 'man') "
            "may indicate inattentive or bot-like responding. Currently context-only. "
            "Consider flagging for manual review when combined with other signals."
        ),
    }


def _analyze_disability_contradiction(scored: pd.DataFrame) -> dict:
    """Analyze disability/autism contradictions."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Explicit "No disability" but >=1 autistic child
    contradiction = scored[
        (scored["Explicit no disability response"] == 1) &
        (scored["Autistic children minimum"].fillna(0) >= 1)
    ]
    contradiction_pay = pay_now[
        (pay_now["Explicit no disability response"] == 1) &
        (pay_now["Autistic children minimum"].fillna(0) >= 1)
    ]

    return {
        "rule_id": "R15 (existing) + Candidate D",
        "item": "Disability and autism answer contradiction",
        "field": "fif_child_needs, fif_num_autistic",
        "finding": (
            f"Records saying 'No disability' + >=1 autistic child: {len(contradiction)}. "
            f"Of these, {len(contradiction_pay)} currently classified as 'Pay now'. "
            "This is currently a review flag (R15) but doesn't block payment by itself."
        ),
        "proposed_refinement": (
            "Reports 'No' to having a child with a disability but elsewhere identifies "
            ">=1 autistic child. May indicate misunderstanding of the question OR "
            "inattentive responding. Should be treated as a strong manual-review signal."
        ),
    }


def _analyze_free_text(scored: pd.DataFrame, sources) -> dict:
    """Analyze free-text response quality across all records."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Check for very short narratives among Pay Now records
    short_narrative = pay_now[
        (pay_now["Narrative characters"].fillna(0) > 0) &
        (pay_now["Narrative characters"].fillna(0) < 20)
    ]
    no_narrative = pay_now[pay_now["Narrative characters"].fillna(0) == 0]

    # Check for placeholder/nonsense patterns in the raw data
    placeholder_count = 0
    repeated_char_count = 0

    for pid, records in sources.records_by_pid.items():
        meta = sources.metadata_by_pid[pid]
        meta_dict = {str(r["field_name"]): r for r in meta.to_dict("records")}
        text_fields = [f for f, r in meta_dict.items()
                       if f in records and r.get("field_type") in {"text", "notes"}
                       and re.search(r"comment|explain|feedback|reason|firstsigns_what|ethic_details|whylater", f)]
        for field in text_fields:
            vals = records[field].fillna("").astype(str)
            # Check for placeholder patterns
            placeholders = vals[vals.str.lower().str.strip().isin(
                {"n/a", "na", "none", "no", ".", "-", "--", "x", "xx", "xxx",
                 "nothing", "no comment", "no comments", "idk", "i don't know",
                 "asdf", "asd", "test", "testing", "abc", "123"}
            )]
            placeholder_count += len(placeholders[placeholders.ne("")])

            # Repeated characters like "aaaa", "hhhh", etc
            repeated = vals[vals.str.match(r"^(.)\1{3,}$", na=False)]
            repeated_char_count += len(repeated)

    return {
        "rule_id": "Candidate G (new)",
        "item": "Free-text response quality",
        "field": "All comment/explain/feedback/reason free-text fields",
        "finding": (
            f"Pay Now with very short narrative (<20 chars): {len(short_narrative)}. "
            f"Pay Now with no narrative at all: {len(no_narrative)}. "
            f"Placeholder-type responses across all studies: {placeholder_count}. "
            f"Repeated-character responses: {repeated_char_count}."
        ),
        "proposed_refinement": (
            "Flag records where free-text fields contain only placeholder responses "
            "(e.g., 'n/a', 'asdf', single characters) or repeated characters. "
            "Do not reject short but valid answers solely because of length."
        ),
    }


def _analyze_children_consistency(scored: pd.DataFrame) -> dict:
    """Check number of children consistency."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Cases where autistic count > total children
    autistic_exceeds = scored[
        scored["Autistic minimum exceeds child maximum"].fillna(0) == 1
    ]
    autistic_exceeds_pn = pay_now[
        pay_now["Autistic minimum exceeds child maximum"].fillna(0) == 1
    ]

    # Cases where age bands > child count
    bands_exceed = scored[
        scored["Child age bands exceed exact count"].fillna(0) == 1
    ]
    bands_exceed_pn = pay_now[
        pay_now["Child age bands exceed exact count"].fillna(0) == 1
    ]

    return {
        "rule_id": "R8 (existing) + Candidate E",
        "item": "Number of children consistency",
        "field": "fif_num_children, fif_num_autistic, fif_childrens_ages",
        "finding": (
            f"Autistic count > total children: {len(autistic_exceeds)} "
            f"({len(autistic_exceeds_pn)} in Pay Now). "
            f"Age bands > child count: {len(bands_exceed)} "
            f"({len(bands_exceed_pn)} in Pay Now)."
        ),
        "proposed_refinement": (
            "Strengthen the existing R8 check. If the number of autistic children "
            "exceeds total children, this is a definite logical impossibility. "
            "Consider making this a hard exclusion rather than a review flag."
        ),
    }


def _analyze_demographics(scored: pd.DataFrame) -> dict:
    """Analyze demographic plausibility issues."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    bad_age = scored[
        (scored["Reported caregiver age"].notna()) &
        (~scored["Reported caregiver age"].between(18, 100))
    ]
    bad_age_pn = pay_now[
        (pay_now["Reported caregiver age"].notna()) &
        (~pay_now["Reported caregiver age"].between(18, 100))
    ]

    bad_zip = scored[scored["ZIP format invalid"].fillna(0) == 1]
    bad_zip_pn = pay_now[pay_now["ZIP format invalid"].fillna(0) == 1]

    bad_birth = scored[scored["Parent birth interval outside range"].fillna(0) == 1]
    bad_birth_pn = pay_now[pay_now["Parent birth interval outside range"].fillna(0) == 1]

    return {
        "rule_id": "R9 (existing) + Candidate F",
        "item": "Demographic consistency checks",
        "field": "age_confirm_elig, demo_momdob, zip_demo, demo_country, dob_child1",
        "finding": (
            f"Age outside 18-100: {len(bad_age)} ({len(bad_age_pn)} in Pay Now). "
            f"Invalid ZIP: {len(bad_zip)} ({len(bad_zip_pn)} in Pay Now). "
            f"Parent birth interval outside 10-60: {len(bad_birth)} ({len(bad_birth_pn)} in Pay Now)."
        ),
        "proposed_refinement": (
            "Cross-validate birth year vs age, education level vs age, and "
            "child age vs parent age more comprehensively. Do not auto-exclude "
            "plausible differences caused by question wording."
        ),
    }


def _analyze_timing(scored: pd.DataFrame) -> dict:
    """Analyze timing patterns."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Extremely fast completions that still passed
    fast_total = pay_now[
        (pay_now["Whole survey minutes"].notna()) &
        (pay_now["Whole survey minutes"] < 15)
    ]

    # Missing timing data
    no_timing = scored[scored["Sections with a recorded time"] == 0]
    no_timing_pn = pay_now[pay_now["Sections with a recorded time"] == 0]

    # Timing data issues
    timing_issues = scored[scored["Timing data issue"] == 1]
    timing_issues_pn = pay_now[pay_now["Timing data issue"] == 1]

    # Overnight submissions (R12)
    overnight = scored[scored["R12"] == 1]
    overnight_pn = pay_now[pay_now["R12"] == 1]

    return {
        "rule_id": "R1-R3, R12 (existing) + Candidate I",
        "item": "Survey timing analysis",
        "field": "get_time_fif, get_time_val, get_time_tfa, get_time_demo, eligibility_timestamp",
        "finding": (
            f"Pay Now with total < 15 min: {len(fast_total)}. "
            f"No section timing at all: {len(no_timing)} ({len(no_timing_pn)} in Pay Now). "
            f"Timing data issues (negative values): {len(timing_issues)} ({len(timing_issues_pn)} in Pay Now). "
            f"Overnight start (00:00-04:59): {len(overnight)} ({len(overnight_pn)} in Pay Now)."
        ),
        "proposed_refinement": (
            "Consider study-specific timing thresholds since surveys differ in length. "
            "Missing all timing data on a completed survey should raise a review flag."
        ),
    }


def _analyze_email_patterns(scored: pd.DataFrame) -> dict:
    """Analyze email duplication and quality patterns."""
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    # Shared emails
    shared_email = scored[scored["Responses sharing payment email"] > 1]
    shared_email_pn = pay_now[pay_now["Responses sharing payment email"] > 1]

    # Disposable emails
    disposable = scored[scored["R10"] == 1]
    disposable_pn = pay_now[pay_now["R10"] == 1]

    # Email confirmation mismatch
    email_mismatch = scored[scored["Email confirmation differs"] == 1]
    email_mismatch_pn = pay_now[pay_now["Email confirmation differs"] == 1]

    # No email on completed survey
    no_email = scored[(scored["R11"] == 1)]
    no_email_pn = pay_now[pay_now["R11"] == 1]

    return {
        "rule_id": "R10, R11 (existing) + Candidate H",
        "item": "Email quality and duplication",
        "field": "demo_email, email_elig, demo_email_confirm",
        "finding": (
            f"Shared payment email (>1 records): {len(shared_email)} ({len(shared_email_pn)} in Pay Now). "
            f"Disposable domain: {len(disposable)} ({len(disposable_pn)} in Pay Now). "
            f"Email confirmation mismatch: {len(email_mismatch)} ({len(email_mismatch_pn)} in Pay Now). "
            f"Completed but no email: {len(no_email)} ({len(no_email_pn)} in Pay Now)."
        ),
        "proposed_refinement": (
            "Evaluate combinations of email hash, demographic pattern, timing pattern, "
            "and identical response sequences for cross-record duplication. "
            "A single duplicate signal alone should not auto-exclude."
        ),
    }


def _analyze_cross_record_duplication(scored: pd.DataFrame) -> dict:
    """Analyze cross-record duplication patterns."""
    # Records with identical response fingerprints
    dup_fingerprint = scored[scored["Records sharing response fingerprint"] >= 2]
    dup_fp_pay = scored[
        (scored["Recommended action"] == "Pay now") &
        (scored["Records sharing response fingerprint"] >= 2)
    ]

    # High narrative similarity
    high_sim = scored[
        (scored["Maximum narrative cosine similarity"].fillna(0) > 0.9)
    ]
    high_sim_pay = scored[
        (scored["Recommended action"] == "Pay now") &
        (scored["Maximum narrative cosine similarity"].fillna(0) > 0.9)
    ]

    # Sign-up bursts
    burst = scored[scored["R6"] == 1]
    burst_pay = scored[
        (scored["Recommended action"] == "Pay now") & (scored["R6"] == 1)
    ]

    return {
        "rule_id": "R5-R7 (existing) + Candidate H",
        "item": "Cross-record duplication patterns",
        "field": "Response fingerprint, narrative similarity, signup timestamps",
        "finding": (
            f"Identical response fingerprint (>=2 sharing): {len(dup_fingerprint)} ({len(dup_fp_pay)} in Pay Now). "
            f"High narrative similarity (>0.90): {len(high_sim)} ({len(high_sim_pay)} in Pay Now). "
            f"Sign-up burst (>=2 within 2min): {len(burst)} ({len(burst_pay)} in Pay Now)."
        ),
        "proposed_refinement": (
            "Combine email hash, demographic pattern, timing pattern, "
            "identical response sequence, and repeated free text for a "
            "composite duplication score. Multiple weak signals combined "
            "may warrant manual review."
        ),
    }


# ---------------------------------------------------------------------------
# Part 3: Sample Pay Now records for manual review
# ---------------------------------------------------------------------------

def _sample_pay_now_records(scored: pd.DataFrame, sources) -> list[dict]:
    """Sample Pay Now records from each study for manual review of actual answers."""
    reviews = []
    pay_now = scored[scored["Recommended action"] == "Pay now"]

    for pid in sorted(scored["REDCap PID"].unique()):
        pid_pay = pay_now[pay_now["REDCap PID"].astype(str) == str(pid)]
        if pid_pay.empty:
            continue

        # Sample up to 5 records from each study
        sample = pid_pay.sample(n=min(5, len(pid_pay)), random_state=42)

        raw_records = sources.records_by_pid.get(int(pid), pd.DataFrame())
        meta = sources.metadata_by_pid.get(int(pid), pd.DataFrame())
        meta_dict = {str(r["field_name"]): r for r in meta.to_dict("records")} if not meta.empty else {}

        for _, row in sample.iterrows():
            rec_id = str(row["Record ID"])
            raw = raw_records[raw_records["record_id"].astype(str) == rec_id]
            if raw.empty:
                continue
            raw_row = raw.iloc[0]

            review = {
                "Study PID": pid,
                "Study": row["Study"],
                "Record ID": rec_id,
                "Current classification": row["Recommended action"],
                "Risk score": row["Risk score"],
                "Checks flagged": row["Which checks were flagged"],
                "Survey finished": row["Survey finished"],
                "Whole survey minutes": row.get("Whole survey minutes", ""),
                "Pronoun": row.get("Pronoun response", ""),
                "Gender": row.get("Gender response", ""),
                "Band-Aid selected": row.get("Band-Aid selected", ""),
                "Other attention options": row.get("Other attention options selected", ""),
                "Explicit no disability": row.get("Explicit no disability response", ""),
                "Autistic children min": row.get("Autistic children minimum", ""),
                "Children max": row.get("Children maximum", ""),
                "Caregiver age": row.get("Reported caregiver age", ""),
                "Email masked": _mask_email(str(row.get("Email address", ""))),
                "Narrative length (chars)": row.get("Narrative characters", ""),
            }

            # Check key raw fields for suspicious patterns
            issues = []
            # Check elig_knee (attention check) in raw data
            knee_fields = [f for f in raw_row.index if f.startswith("elig_knee")]
            if knee_fields:
                knee_vals = {f: raw_row[f] for f in knee_fields if str(raw_row[f]).strip() not in ("", "nan", "0")}
                band_code = _match_code(meta_dict, "elig_knee", r"band[ -]?aid|curita")
                if knee_vals:
                    selected_codes = [f.split("___")[-1] if "___" in f else str(raw_row[f]) for f in knee_vals]
                    if band_code and band_code not in selected_codes:
                        issues.append(f"Did NOT select Band-Aid (selected codes: {selected_codes})")
                    elif len(knee_vals) > 1:
                        issues.append(f"Selected multiple attention options: {list(knee_vals.keys())}")

            # Check pronoun/gender mismatch
            if row.get("She and man pairing", 0) == 1:
                issues.append("She/Her pronouns + Man gender")
            if row.get("He and woman pairing", 0) == 1:
                issues.append("He/Him pronouns + Woman gender")

            # Check disability contradiction
            if row.get("Explicit no disability response", 0) == 1 and (row.get("Autistic children minimum", 0) or 0) >= 1:
                issues.append("Said 'No disability' but has autistic child(ren)")

            review["Issues found"] = "; ".join(issues) if issues else "None detected"
            reviews.append(review)

    return reviews


# ---------------------------------------------------------------------------
# Part 4: Build the combined rule list for Jessica
# ---------------------------------------------------------------------------

def build_combined_rule_list(findings: list[dict]) -> pd.DataFrame:
    """Build the final combined rule list in Jessica's requested format.

    Format:
        Item | Field Name | Flag
    """
    rows = []

    # --- Existing implemented rules ---
    existing_rules = [
        {"Item": "Survey completion speed (whole survey)", "Field Name": "get_time_fif, get_time_val, get_time_tfa, get_time_demo", "Flag": f"Total of 4 section times < {TOTAL_TIME_FLOOR_MIN} min — flags unusually fast completion", "Rule ID": "R1", "Status": "Implemented"},
        {"Item": "Thoughts & Feelings section speed", "Field Name": "get_time_tfa", "Flag": f"TFA section time < {TFA_TIME_FLOOR_MIN} min", "Rule ID": "R2", "Status": "Implemented"},
        {"Item": "Any section below time floor", "Field Name": "get_time_fif, get_time_val, get_time_tfa, get_time_demo", "Flag": "Individual section below floor (FIF<2.55, Val<0.22, TFA<6.67, Demo<0.95 min)", "Rule ID": "R3", "Status": "Implemented"},
        {"Item": "Straight-lining in rating blocks", "Field Name": "Values and TFA rating-scale items", "Flag": "Rating block with >=4 items, >=80% answered, and SD < 0.15 — same answer repeated", "Rule ID": "R4", "Status": "Implemented"},
        {"Item": "Identical response fingerprint", "Field Name": "All rating-scale items (SHA-256 hash)", "Flag": "Exact match on >=20 rating items with another record in same study", "Rule ID": "R5", "Status": "Implemented"},
        {"Item": "Sign-up burst (cluster of submissions)", "Field Name": "eligibility_timestamp", "Flag": ">=2 other records started within ±120 seconds in same study", "Rule ID": "R6", "Status": "Implemented"},
        {"Item": "Highly similar written responses", "Field Name": "Free-text comment/explain/feedback fields", "Flag": "TF-IDF cosine similarity > 0.90 on narratives (>=100 chars, >=20 words)", "Rule ID": "R7", "Status": "Implemented"},
        {"Item": "Family count / branching inconsistency", "Field Name": "fif_num_autistic, fif_num_children, fif_childrens_ages", "Flag": "Impossible combinations: autistic > total children, age bands > child count, zero autism with gated follow-up answered", "Rule ID": "R8", "Status": "Implemented"},
        {"Item": "Age or postal information", "Field Name": "age_confirm_elig, demo_momdob, zip_demo, dob_child1", "Flag": "Age outside 18-100, malformed US ZIP, or parent-birth-year interval outside 10-60", "Rule ID": "R9", "Status": "Implemented"},
        {"Item": "Temporary/disposable email domain", "Field Name": "demo_email, email_elig", "Flag": "Email domain on known disposable/temporary list (e.g., mailinator, guerrillamail)", "Rule ID": "R10", "Status": "Implemented"},
        {"Item": "Completed survey with no email", "Field Name": "demo_email, email_elig", "Flag": "All 4 sections complete but no participant email provided", "Rule ID": "R11", "Status": "Implemented"},
        {"Item": "Overnight survey start", "Field Name": "eligibility_timestamp", "Flag": "Survey started between midnight and 5am (REDCap local time)", "Rule ID": "R12", "Status": "Implemented"},
        {"Item": "What does a parent usually do in the situation pictured below? (Band-Aid attention check)", "Field Name": "elig_knee", "Flag": "Not checking 'Puts on a Band-Aid' or checking that in addition to other responses", "Rule ID": "R13", "Status": "Implemented"},
        {"Item": "Pronouns and Gender", "Field Name": "pronouns_elig, demo_gender", "Flag": "Mismatched pronoun and gender responses (e.g., selecting 'she' and 'man')", "Rule ID": "R14", "Status": "Implemented"},
        {"Item": "Disability vs. autism contradiction", "Field Name": "fif_child_needs, fif_num_autistic", "Flag": "Reports 'No' disability but identifies >=1 autistic child", "Rule ID": "R15", "Status": "Implemented"},
    ]

    # --- Proposed new rules ---
    proposed_rules = [
        {"Item": "Free-text contains standalone word 'test'", "Field Name": "All text/notes fields", "Flag": "Record excluded pre-screening if any free-text field contains the standalone word 'test' (case-insensitive). Already applied as a source-level exclusion.", "Rule ID": "Excl-A", "Status": "Implemented (exclusion)"},
        {"Item": "Free-text placeholder/nonsense detection", "Field Name": "All comment/explain/feedback/reason fields", "Flag": "Responses consisting only of placeholder text ('n/a', 'asdf', '123', repeated characters like 'aaaa'). Short but valid answers should NOT be flagged.", "Rule ID": "Proposed-G", "Status": "Proposed — needs approval"},
        {"Item": "Education level vs. age cross-check", "Field Name": "demo_education, age_confirm_elig", "Flag": "Claimed education level is impossible given stated age (e.g., doctoral degree at age 20)", "Rule ID": "Proposed-F1", "Status": "Proposed — needs approval"},
        {"Item": "Duplicate email across studies", "Field Name": "demo_email, email_elig", "Flag": "Same email address used across multiple study PIDs (not just within one study)", "Rule ID": "Proposed-H1", "Status": "Proposed — needs approval"},
        {"Item": "Email confirmation field mismatch", "Field Name": "demo_email, demo_email_confirm / demo_email2", "Flag": "Primary email and confirmation email do not match — may indicate copy-paste error or intentional variation", "Rule ID": "Proposed-H2", "Status": "Implemented (contact hold)"},
        {"Item": "Very fast overall completion (< 5 min)", "Field Name": "Whole survey minutes", "Flag": "Survey completed in under 5 minutes total — extremely unlikely for genuine respondent", "Rule ID": "Proposed-I1", "Status": "Proposed — needs approval"},
        {"Item": "Child birth year vs parent birth year impossible interval", "Field Name": "dob_child1, demo_momdob", "Flag": "Parent was <10 or >60 years old at first child's birth — already partially in R9 but could be its own hard-exclusion rule", "Rule ID": "Proposed-F2", "Status": "Partially implemented in R9"},
        {"Item": "Prenatal testing mutually exclusive branching violation", "Field Name": "fif_pregnant, fif_prenatal_preg, fif_prenatal_no_preg", "Flag": "Answering both pregnant and non-pregnant follow-ups, or answering testing reasons without answering the prenatal gate", "Rule ID": "Proposed-B1", "Status": "Proposed — needs approval"},
        {"Item": "Earlier diagnosis reason answered without gate", "Field Name": "fif_diag_earlier, fif_diag_early_yes, fif_diag_early_no", "Flag": "Submitting reasons for earlier diagnosis when respondent explicitly answered 'No' (or left blank) the gate question asking if child received an earlier diagnosis", "Rule ID": "Proposed-B2", "Status": "Proposed — needs approval"},
        {"Item": "Country and Postal Code format mismatch", "Field Name": "demo_country, zip_demo, demo_state", "Flag": "Selecting US residence but providing non-US/Canadian alphanumeric postal code, or obvious dummy ZIP ('00000', '12345', <5 digits)", "Rule ID": "Proposed-F3", "Status": "Proposed — needs approval"},
        {"Item": "Composite duplication score", "Field Name": "Email hash + demographic pattern + timing + response fingerprint", "Flag": "Multiple weak duplication signals combined (e.g., same email domain + similar timing + similar demographics without being the same person)", "Rule ID": "Proposed-H3", "Status": "Proposed — needs approval"},
        {"Item": "Missing all timing data on complete survey", "Field Name": "get_time_fif/val/tfa/demo, completion status", "Flag": "All 4 sections marked complete but zero section-timing fields available — review for data integrity", "Rule ID": "Proposed-I2", "Status": "Implemented (review concern)"},
        {"Item": "Non-numeric or impossible number-of-children response", "Field Name": "fif_num_children", "Flag": "Number of children field contains non-numeric value or impossible number (e.g., 0 children for a 'caregiver' survey)", "Rule ID": "Proposed-E1", "Status": "Proposed — needs approval"},
    ]

    rows = existing_rules + proposed_rules
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Pull fresh data from REDCap")
    args = parser.parse_args()

    print("Loading data from refined pipeline...")
    sources = load_refined_sources(PROJECT_DIR, refresh=args.refresh)
    scored = score_refined_records(sources)

    print(f"\nTotal records analyzed: {len(scored)}")
    print(f"Pay now: {(scored['Recommended action'] == 'Pay now').sum()}")
    print(f"Check by hand: {(scored['Recommended action'] == 'Check by hand').sum()}")
    print(f"Do not pay: {(scored['Recommended action'] == 'Do not pay').sum()}")
    print(f"Incomplete: {(scored['Recommended action'] == 'Incomplete - not eligible').sum()}")

    print("\n" + "=" * 80)
    print("EXAMINING ACTUAL PARTICIPANT RESPONSES")
    print("=" * 80)

    results = examine_responses(sources, scored)

    # Print pattern findings
    print("\n--- Pattern Findings from Actual Responses ---\n")
    for finding in results["pattern_findings"]:
        print(f"\n{'─' * 70}")
        print(f"Rule: {finding['rule_id']}")
        print(f"Item: {finding['item']}")
        print(f"Field: {finding['field']}")
        print(f"Finding: {finding['finding']}")
        print(f"Proposed: {finding['proposed_refinement']}")

    # Print sample reviews
    print(f"\n\n{'=' * 80}")
    print("SAMPLE PAY NOW RECORDS FOR MANUAL REVIEW")
    print(f"{'=' * 80}\n")
    for review in results["sample_reviews"][:15]:
        print(f"\n--- PID {review['Study PID']}, Record {review['Record ID']} ---")
        for key, val in review.items():
            if key not in ("Study PID", "Record ID"):
                print(f"  {key}: {_short(str(val), 120)}")

    # Build and print the combined rule list
    print(f"\n\n{'=' * 80}")
    print("COMBINED RULE LIST (Existing + Proposed)")
    print("Format: Item | Field Name | Flag")
    print(f"{'=' * 80}\n")

    rule_list = build_combined_rule_list(results["pattern_findings"])

    # Print as a formatted table
    pd.set_option("display.max_colwidth", 100)
    pd.set_option("display.width", 200)
    print(rule_list[["Item", "Field Name", "Flag", "Status"]].to_string(index=False))

    # Save to CSV
    output_path = PROJECT_DIR / "Caregiver Outputs" / "response_review_rule_list.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rule_list.to_csv(output_path, index=False)
    print(f"\n\nRule list saved to: {output_path}")

    # Save detailed findings
    findings_path = PROJECT_DIR / "Caregiver Outputs" / "response_review_findings.csv"
    pd.DataFrame(results["pattern_findings"]).to_csv(findings_path, index=False)
    print(f"Detailed findings saved to: {findings_path}")

    # Save sample reviews
    reviews_path = PROJECT_DIR / "Caregiver Outputs" / "sample_pay_now_reviews.csv"
    pd.DataFrame(results["sample_reviews"]).to_csv(reviews_path, index=False)
    print(f"Sample reviews saved to: {reviews_path}")

    return rule_list, results


if __name__ == "__main__":
    main()
