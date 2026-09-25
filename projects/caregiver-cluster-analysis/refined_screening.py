"""Metadata-aware response screening; observed risk is not a verified bot identity.

The supplied 15-rule weighted score is retained for audit/comparison. Payment
recommendations use corroborating evidence families, so overlapping timing
checks, recruitment bursts, and identity descriptions cannot manufacture a
refusal. These are operational recommendations, not completed payments.
"""
from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict

import numpy as np
import pandas as pd

TOTAL_TIME_FLOOR_MIN = 11.57
TFA_TIME_FLOOR_MIN = 7.85
SECTION_FLOORS_MIN = {"fif": 2.55, "val": 0.22, "tfa": 6.67, "demo": 0.95}
BURST_WINDOW_SECONDS = 120
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "temp-mail.org",
    "yopmail.com", "throwam.com", "fakeinbox.com", "trashmail.com",
    "dispostable.com", "maildrop.cc",
}
COMPLETION_FIELDS = ["family_information_form_complete", "values_complete",
                     "tfa_complete", "demographics_complete"]
RULES = {
    "R1": {"label": "Whole survey below configured time floor", "points": 2, "family": "Timing", "description": "All four nonnegative section times recorded; sum < 11.57 minutes. Shares one evidence family with R2."},
    "R2": {"label": "Thoughts and feelings below configured time floor", "points": 2, "family": "Timing", "description": "Nonnegative Thoughts and Feelings time < 7.85 minutes. Shares one evidence family with R1."},
    "R3": {"label": "A section below its configured time floor", "points": 1, "family": "Timing", "description": "Recorded FIF <2.55, Values <0.22, TFA <6.67, or Demographics <0.95 minutes. Supporting context; not separate corroboration."},
    "R4": {"label": "Repeated answers in a rating block", "points": 1, "family": "Copying", "description": "Within a metadata-defined rating block, >=80% and >=4 items answered, sample SD < 0.15. Supporting context only."},
    "R5": {"label": "Identical response pattern", "points": 2, "family": "Copying", "description": "Exact SHA-256 match on >=20 metadata-defined rating items, >=80% valid answers, within the same study."},
    "R6": {"label": "At least two other sign-ups within two minutes", "points": 2, "family": "Context", "description": "At least two other records in the same study arrived within +/-120 seconds. Recruitment context, not individual fraud evidence."},
    "R7": {"label": "Highly similar substantive written responses", "points": 1, "family": "Copying", "description": "TF-IDF 1-2 gram cosine >0.90 on participant narrative >=100 characters and >=20 words. Short generic comments excluded."},
    "R8": {"label": "Family count or branching inconsistency", "points": 2, "family": "Logic", "description": "Metadata-gated autism follow-ups despite zero autistic children, too many selected child age bands, or autistic count exceeding total child count. Only impossible count bounds supply independent evidence."},
    "R9": {"label": "Age or postal information needs checking", "points": 2, "family": "Review", "description": "Reported age outside 18-100, malformed nonempty US ZIP, or parent birth-year interval outside 10-60. These are data/eligibility concerns, not proof of fraud."},
    "R10": {"label": "Temporary email domain", "points": 1, "family": "Context", "description": "Email domain on the documented temporary-domain list; requires a usable payment contact."},
    "R11": {"label": "Completed response has no email", "points": 1, "family": "Contact", "description": "All four sections completed and no participant email in demographics or eligibility."},
    "R12": {"label": "Started between midnight and five", "points": 1, "family": "Context", "description": "Eligibility timestamp hour 0-4 in REDCap local time. Caregiver work/sleep schedules are not fraud evidence."},
    "R13": {"label": "Band-Aid attention response needs checking", "points": 2, "family": "Attention", "description": "Answered attention check differs from Band-Aid alone. Missing/unanswered is not a failure. No-image/prefer-not-to-answer selections are accessibility/review concerns."},
    "R14": {"label": "Pronoun and gender pairing (context only)", "points": 2, "family": "Context", "description": "She+Man or He+Woman according to actual choice labels. These self-descriptions can coexist; never blocks payment or supplies fraud evidence."},
    "R15": {"label": "Disability and autism answers need clarification", "points": 2, "family": "Review", "description": "Explicit No disability response and >=1 autistic child. Different interpretations of disability are possible; review only, never independent fraud evidence."},
}


def _s(df, field):
    if field not in df:
        return pd.Series("", index=df.index, dtype=str)
    return df[field].astype("string").fillna("").str.strip().replace({"nan": "", "<NA>": "", "None": ""}).astype(str)


def _n(df, field):
    return pd.to_numeric(_s(df, field), errors="coerce")


def _plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value)))).strip()


def _code(value):
    text = str(value).strip()
    return re.sub(r"\.0$", "", text)


def _choices(row):
    if row.get("field_type") == "yesno":
        return {"1": "Yes", "0": "No"}
    parts = re.split(r"\s*\|\s*|\r?\n", str(row.get("select_choices_or_calculations", "")))
    return {_code(a): _plain(b) for part in parts if "," in part for a, b in [part.split(",", 1)]}


def _match_code(meta, field, pattern):
    choices = _choices(meta.get(field, {}))
    found = [code for code, label in choices.items() if re.search(pattern, label, re.I)]
    return found[0] if len(found) == 1 else None


def _count_bounds(df, meta, field):
    """Decode labels, not numeric codes: 'More than 3' means [4, infinity]."""
    bounds = {}
    for code, label in _choices(meta.get(field, {})).items():
        if re.fullmatch(r"\d+", label):
            bounds[code] = (float(label), float(label))
        elif re.fullmatch(r"\d+\s*\+", label):
            bounds[code] = (float(re.search(r"\d+", label)[0]), np.inf)
        elif re.search(r"more than|más de", label, re.I):
            match = re.search(r"\d+", label)
            if match:
                bounds[code] = (float(match[0]) + 1, np.inf)
    values = _s(df, field).map(_code)
    return (values.map({k: v[0] for k, v in bounds.items()}),
            values.map({k: v[1] for k, v in bounds.items()}))


def _rating_features(df, meta):
    fields, blocks, limits = [], defaultdict(list), {}
    for field, row in meta.items():
        if field not in df or row.get("form_name") not in {"values", "tfa"}:
            continue
        choices = _choices(row)
        codes = sorted(float(k) for k in choices if re.fullmatch(r"\d+", k))
        if choices and len(codes) == len(choices) and codes in [list(range(1, k + 1)) for k in range(4, 8)]:
            labels = " ".join(choices.values()).lower()
            # Consecutive response codes alone do not make a scale: knowledge
            # percentages, ages and categorical preferences also use 1..N.
            ordinal_labels = re.search(r"like me|difficult|easy|doable|important|helpful|right|wrong|agree|likely|sure|certain|often|always|never", labels)
            opt_out = re.search(r"don.t want|do not want|would not|prefer not", labels)
            if not ordinal_labels or opt_out:
                continue
            low, high = 1., max(codes)
        elif row.get("field_type") in {"slider", "text"}:
            try:
                low, high = float(row.get("text_validation_min", "")), float(row.get("text_validation_max", ""))
            except (TypeError, ValueError):
                continue
            if low != 1 or high not in {4, 5, 6, 7}:
                continue
        else:
            continue
        fields.append(field)
        limits[field] = (low, high)
        block = (row.get("form_name"), row.get("matrix_group_name") or (row.get("field_type"), _plain(row.get("select_choices_or_calculations", "")), high))
        blocks[block].append(field)
    fields = sorted(fields)
    numeric = pd.DataFrame({f: _n(df, f).where(_n(df, f).between(*limits[f])) for f in fields}, index=df.index)
    sds = []
    for columns in blocks.values():
        if len(columns) >= 4:
            block = numeric[columns]
            eligible = block.notna().sum(axis=1).ge(4) & block.notna().mean(axis=1).ge(.8)
            sds.append(block.std(axis=1, ddof=1).where(eligible))
    minimum_sd = pd.concat(sds, axis=1).min(axis=1) if sds else pd.Series(np.nan, index=df.index)
    fraction = numeric.notna().mean(axis=1) if fields else pd.Series(0., index=df.index)
    fingerprint = (numeric.astype(object).where(numeric.notna(), "<NA>").astype(str).agg("|".join, axis=1)
                   .map(lambda x: hashlib.sha256(x.encode()).hexdigest())) if fields else pd.Series("", index=df.index)
    counts = fingerprint.map(fingerprint[fraction.ge(.8)].value_counts()).fillna(0).astype(int)
    diverse = numeric.nunique(axis=1).ge(3)
    return minimum_sd, fraction, fingerprint, counts, diverse, fields


def _text_similarity(df, meta):
    fields = [f for f, r in meta.items() if f in df and r.get("field_type") in {"text", "notes"}
              and re.search(r"comment|explain|feedback|reason|firstsigns_what|ethic_details|whylater", f)
              and not r.get("text_validation_type_or_show_slider_number")]
    joined = df[fields].fillna("").astype(str).agg(" ".join, axis=1).map(_plain) if fields else pd.Series("", index=df.index)
    enough = joined.str.len().ge(100) & joined.str.split().str.len().ge(20)
    similarity = pd.Series(np.nan, index=df.index)
    if enough.sum() >= 2:
        from sklearn.feature_extraction.text import TfidfVectorizer
        matrix = TfidfVectorizer(lowercase=True, strip_accents="unicode", ngram_range=(1, 2)).fit_transform(joined[enough])
        pairs = matrix @ matrix.T
        pairs.setdiag(0)
        similarity.loc[enough] = np.asarray(pairs.max(axis=1).toarray()).ravel()
    elif enough.any():
        similarity.loc[enough] = 0.
    return similarity, joined.str.len(), joined.str.split().str.len()


def _timestamps(df):
    start = pd.to_datetime(_s(df, "eligibility_timestamp"), errors="coerce", format="mixed")
    columns = [f.replace("_complete", "_timestamp") for f in COMPLETION_FIELDS
               if f.replace("_complete", "_timestamp") in df]
    ends = pd.DataFrame({f: pd.to_datetime(_s(df, f), errors="coerce", format="mixed") for f in columns}, index=df.index)
    end = ends.max(axis=1) if columns else pd.Series(pd.NaT, index=df.index)
    return start, end


def _arrival_counts(ts):
    valid = ts.dropna().sort_values()
    counts = pd.Series(0, index=ts.index, dtype=int)
    if len(valid):
        epoch = valid.to_numpy().astype("datetime64[s]").astype(np.int64)
        counts.loc[valid.index] = np.searchsorted(epoch, epoch + 120, side="right") - np.searchsorted(epoch, epoch - 120, side="left") - 1
    return counts


def decision_gate(completed, independent_families, review_concern, contact_hold, source_issue=False):
    if not bool(completed):
        return "Incomplete - not eligible"
    if bool(source_issue):
        return "Check by hand"
    if int(independent_families) >= 2:
        return "Do not pay"
    if bool(review_concern) or bool(contact_hold):
        return "Check by hand"
    return "Pay now"


def score_study(df, study_label, metadata, *, project_id=None, provenance_verified=False):
    """Score canonical EN-code responses using this project's actual metadata."""
    df = df.reset_index(drop=True).copy()
    if "record_id" not in df or not _s(df, "record_id").is_unique:
        raise ValueError("Scoring requires one unique row per record in each project")
    meta = {str(r["field_name"]): r for r in metadata.to_dict("records")}
    out = pd.DataFrame(index=df.index)
    out["Study"] = study_label
    out["REDCap PID"] = project_id
    out["Record ID"] = _s(df, "record_id")
    out["Response key"] = str(project_id) + ":" + out["Record ID"]
    out["Source project verified"] = "Yes" if provenance_verified else "No"
    completed = pd.DataFrame({f: _n(df, f).eq(2) for f in COMPLETION_FIELDS}).all(axis=1)
    out["Survey finished"] = np.where(completed, "Yes", "No")
    for field in COMPLETION_FIELDS:
        out[field] = _n(df, field)
    email = _s(df, "demo_email").where(_s(df, "demo_email").ne(""), _s(df, "email_elig")).str.lower()
    domain = email.str.rsplit("@", n=1).str[-1].where(email.str.contains("@"), "")
    usable = email.str.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+")
    confirmation = _s(df, "demo_email_confirm").str.lower()
    if "demo_email_confirm" not in df:
        confirmation = _s(df, "demo_email2").str.lower()
    confirmation_mismatch = confirmation.ne("") & email.ne("") & email.ne(confirmation)
    out["Email address"] = email
    out["Email usable"] = np.where(usable, "Yes", "No")
    out["University email address"] = np.where(domain.isin({"sc.edu", "uscmed.sc.edu", "mailbox.sc.edu"}), "Yes", "No")
    out["Email confirmation differs"] = confirmation_mismatch.astype(int)
    out["Email domain"] = domain
    start, end = _timestamps(df)
    out["Started"] = start.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    out["Last section handed in"] = end.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    out["Start hour"] = start.dt.hour
    out["Minutes from sign-up to last section"] = (end - start).dt.total_seconds().div(60).where(end.ge(start))
    times = pd.DataFrame({s: _n(df, f"get_time_{s}").where(_n(df, f"get_time_{s}").ge(0)) for s in SECTION_FLOORS_MIN})
    names = {"fif": "Family Information minutes", "val": "Values minutes", "tfa": "Thoughts and feelings minutes", "demo": "Demographics minutes"}
    for section, name in names.items():
        out[name] = times[section]
    out["Whole survey minutes"] = times.sum(axis=1, min_count=4)
    out["Sections with a recorded time"] = times.notna().sum(axis=1)
    out["Timing data issue"] = pd.DataFrame({s: _n(df, f"get_time_{s}").lt(0) for s in SECTION_FLOORS_MIN}).any(axis=1).astype(int)

    flags, evaluated, evidence = {}, {}, {}
    def rule(code, flag, available, details, absent=False):
        flags[code] = flag.fillna(False).astype(bool) if isinstance(flag, pd.Series) else pd.Series(bool(flag), index=df.index)
        evaluated[code] = available.fillna(False).astype(bool) if isinstance(available, pd.Series) else pd.Series(bool(available), index=df.index)
        evidence[code] = details if isinstance(details, pd.Series) else pd.Series(details, index=df.index)
        out[code] = flags[code].astype(int)
        out[f"{code} status"] = np.where(flags[code], "Flagged", np.where(evaluated[code], "Clear", "Not applicable" if absent else "Not evaluable"))
        out[f"{code} evidence"] = evidence[code]

    rule("R1", out["Whole survey minutes"].lt(TOTAL_TIME_FLOOR_MIN), times.notna().all(axis=1), out["Whole survey minutes"].map(lambda v: f"Recorded total={v:.5g} min; floor=11.57" if pd.notna(v) else "Four valid section times unavailable"))
    rule("R2", times.tfa.lt(TFA_TIME_FLOOR_MIN), times.tfa.notna(), times.tfa.map(lambda v: f"Recorded TFA={v:.5g} min; floor=7.85" if pd.notna(v) else "TFA timing unavailable"))
    lows = pd.DataFrame({s: times[s].lt(f) for s, f in SECTION_FLOORS_MIN.items()})
    rule("R3", lows.any(axis=1), times.notna().any(axis=1), lows.apply(lambda row: "; ".join(s for s in row.index if row[s]) or "No observed section below floor", axis=1))
    sd, fraction, fingerprint, duplicate_counts, diverse, rating_fields = _rating_features(df, meta)
    out["Minimum rating block SD"] = sd
    out["Rating items available"] = len(rating_fields)
    out["Rating fraction answered"] = fraction
    out["Response fingerprint"] = fingerprint
    out["Records sharing response fingerprint"] = duplicate_counts
    out["Rating pattern has at least three values"] = diverse.astype(int)
    rule("R4", sd.lt(.15), sd.notna(), sd.map(lambda v: f"Minimum comparable block SD={v:.5g}" if pd.notna(v) else "No rating block with >=4 and >=80% valid answers"))
    fp_evaluable = fraction.ge(.8) & (len(rating_fields) >= 20)
    rule("R5", duplicate_counts.ge(2) & fp_evaluable, fp_evaluable, duplicate_counts.map(lambda n: f"{n} records share exact fingerprint; item count={len(rating_fields)}; >=80% coverage required"))
    neighbours = _arrival_counts(start)
    out["Other sign-ups within two minutes"] = neighbours
    rule("R6", neighbours.ge(2) & start.notna(), start.notna(), neighbours.map(lambda n: f"{n} other records within +/-120 seconds in this PID"))
    similarity, chars, words = _text_similarity(df, meta)
    out["Maximum narrative cosine similarity"] = similarity
    out["Narrative characters"] = chars
    out["Narrative words"] = words
    rule("R7", similarity.gt(.9), similarity.notna(), similarity.map(lambda v: f"Maximum substantive narrative cosine={v:.5g}" if pd.notna(v) else "No substantive narrative with >=100 characters and >=20 words"))

    autistic_low, autistic_high = _count_bounds(df, meta, "fif_num_autistic")
    children_low, children_high = _count_bounds(df, meta, "fif_num_children")
    agecols = [f"fif_childrens_ages___{c}" for c in _choices(meta.get("fif_childrens_ages", {})) if f"fif_childrens_ages___{c}" in df]
    agebands = df[agecols].apply(pd.to_numeric, errors="coerce").eq(1).sum(axis=1) if agecols else pd.Series(0, index=df.index)
    fu_fields = []
    for field, row in meta.items():
        logic = str(row.get("branching_logic", ""))
        # Accept only the documented positive-count gate; never infer from name.
        gated = re.search(r'\[fif_num_autistic\]\s*(?:>\s*[\'\"]?\d+[\'\"]?|<>\s*[\'\"]?0[\'\"]?)', logic)
        # An alternative OR path may legitimately show the follow-up at zero.
        if (not gated or re.search(r'\bor\b', logic, re.I)
                or row.get("field_type") in {"descriptive", "calc"}
                or "@CALCTEXT" in str(row.get("field_annotation", "")).upper()):
            continue
        if row.get("field_type") == "checkbox":
            fu_fields.extend(c for c in df if c.startswith(field + "___"))
        elif field in df:
            fu_fields.append(field)
    answered_fu = pd.DataFrame({f: _n(df, f).eq(1) if "___" in f else _s(df, f).ne("") for f in fu_fields}, index=df.index).any(axis=1)
    branches = autistic_high.eq(0) & answered_fu
    too_many_bands = children_high.notna() & agebands.gt(children_high)
    count_conflict = autistic_low.notna() & children_high.notna() & autistic_low.gt(children_high)
    out["Autistic children minimum"] = autistic_low
    out["Children maximum"] = children_high.replace(np.inf, np.nan)
    out["Children count is open ended"] = children_high.eq(np.inf).astype(int)
    out["Selected child age bands"] = agebands
    out["Autism follow-up answered"] = answered_fu.astype(int)
    out["Zero autism with gated follow-up"] = branches.astype(int)
    out["Child age bands exceed exact count"] = too_many_bands.astype(int)
    out["Autistic minimum exceeds child maximum"] = count_conflict.astype(int)
    family_reason = pd.Series("No observed family inconsistency", index=df.index)
    for i in df.index:
        bits = []
        if branches.at[i]: bits.append("Zero autistic children with gated follow-up stored; may be stale hidden response")
        if too_many_bands.at[i]: bits.append(f"{agebands.at[i]} age bands > maximum {children_high.at[i]:g} children")
        if count_conflict.at[i]: bits.append(f"Autistic minimum {autistic_low.at[i]:g} > total maximum {children_high.at[i]:g}")
        if bits: family_reason.at[i] = "; ".join(bits)
    rule("R8", branches | too_many_bands | count_conflict, autistic_low.notna() | children_high.notna(), family_reason)

    age = _n(df, "age_confirm_elig") if "age_confirm_elig" in meta else pd.Series(np.nan, index=df.index)
    respondent_birth = pd.to_datetime(_s(df, "demo_momdob"), errors="coerce", format="mixed")
    birthday_pending = ((start.dt.month < respondent_birth.dt.month)
                        | ((start.dt.month == respondent_birth.dt.month)
                           & (start.dt.day < respondent_birth.dt.day)))
    derived_age = (start.dt.year - respondent_birth.dt.year - birthday_pending.astype(int)).where(
        start.notna() & respondent_birth.notna())
    out["Caregiver age source"] = np.where(age.notna(), "Reported eligibility age",
        np.where(derived_age.notna(), "Date of birth and survey start date", "Unavailable"))
    age = age.fillna(derived_age)
    bad_age = age.notna() & ~age.between(18, 100)
    country_code = _match_code(meta, "demo_country", r"^(yes|us|united states)$")
    us = _s(df, "demo_country").map(_code).eq(country_code) if country_code else pd.Series(False, index=df.index)
    zipcode = _s(df, "zip_demo")
    bad_zip = us & zipcode.ne("") & ~zipcode.str.fullmatch(r"\d{5}(?:-\d{4})?")
    # First-child year yields an interval, allowing rounding and birthdays.
    # It is a plausibility review, because caregivers may be adoptive/step parents.
    birth_year = _n(df, "dob_child1") if "dob_child1" in meta else pd.Series(np.nan, index=df.index)
    lower = birth_year - respondent_birth.dt.year - 1
    upper = birth_year - respondent_birth.dt.year
    bad_birth = lower.notna() & upper.notna() & (upper.lt(10) | lower.gt(60))
    out["Reported caregiver age"] = age
    out["US residence selected"] = us.astype(int)
    out["ZIP provided"] = zipcode
    out["ZIP format invalid"] = bad_zip.astype(int)
    out["Parent age at first birth lower"] = lower
    out["Parent age at first birth upper"] = upper
    out["Parent birth interval outside range"] = bad_birth.astype(int)
    demo_reason = pd.Series("No observed age/postal plausibility concern", index=df.index)
    for i in df.index:
        bits = []
        if bad_age.at[i]: bits.append(f"Reported age {age.at[i]:g} outside 18-100")
        if bad_zip.at[i]: bits.append("Nonempty US ZIP is not 5 digits or ZIP+4")
        if bad_birth.at[i]: bits.append(f"Parent age interval at first child birth [{lower.at[i]:g}, {upper.at[i]:g}] outside 10-60; family role needs clarification")
        if bits: demo_reason.at[i] = "; ".join(bits)
    rule("R9", bad_age | bad_zip | bad_birth, age.notna() | (us & zipcode.ne("")) | lower.notna(), demo_reason)
    disposable = domain.isin(DISPOSABLE_DOMAINS)
    rule("R10", disposable, email.ne(""), np.where(disposable, "Temporary-domain list match; contact review only", "No listed temporary-domain match"))
    rule("R11", completed & email.eq(""), completed, np.where(email.eq(""), "No participant email available", "Participant email available"))
    rule("R12", start.dt.hour.between(0, 4), start.notna(), np.where(start.dt.hour.between(0, 4), "Local REDCap start hour 00:00-04:59; context only", "Not observed in overnight interval"))

    out = out.copy()  # Consolidate the evidence columns before adding the final checks.
    knee = meta.get("elig_knee")
    band_code = _match_code(meta, "elig_knee", r"band[ -]?aid|curita")
    if knee is not None and band_code is None:
        raise ValueError(f"PID {project_id}: Band-Aid choice meaning cannot be uniquely verified.")
    attention_available = knee is not None and band_code is not None
    selections = pd.DataFrame(index=df.index)
    if attention_available and knee.get("field_type") == "checkbox":
        selections = pd.DataFrame({c: _n(df, f"elig_knee___{c}").eq(1) for c in _choices(knee)}, index=df.index)
    elif attention_available:
        selections = pd.DataFrame({c: _s(df, "elig_knee").map(_code).eq(c) for c in _choices(knee)}, index=df.index)
    answered = selections.any(axis=1) if not selections.empty else pd.Series(False, index=df.index)
    selected_band = selections.get(band_code, pd.Series(False, index=df.index))
    other_count = selections.drop(columns=[band_code], errors="ignore").sum(axis=1)
    ambiguous_codes = [c for c, label in _choices(knee or {}).items() if re.search(r"no image|prefer not|no hay imagen|prefiero no", label, re.I)]
    access_issue = selections.reindex(columns=ambiguous_codes, fill_value=False).any(axis=1)
    attention_flag = answered & (~selected_band | other_count.gt(0))
    attention_strong = attention_flag & ~access_issue
    out["Attention check available"] = int(attention_available)
    out["Attention check answered"] = answered.astype(int)
    out["Band-Aid selected"] = selected_band.astype(int)
    out["Other attention options selected"] = other_count
    out["Attention accessibility or opt-out response"] = access_issue.astype(int)
    knee_reason = pd.Series("Question absent or Band-Aid choice not verified", index=df.index)
    if attention_available:
        knee_reason = selections.apply(lambda r: "; ".join(_choices(knee)[c] for c in r.index if r[c]) or "Unanswered checkbox group; not treated as failure", axis=1)
    rule("R13", attention_flag, answered, knee_reason, absent=not attention_available)

    pronoun = _s(df, "pronouns_elig").map(_code)
    gender = _s(df, "demo_gender").map(_code)
    she = _match_code(meta, "pronouns_elig", r"^she/|^ella$")
    he = _match_code(meta, "pronouns_elig", r"^he/|^él$")
    man = _match_code(meta, "demo_gender", r"^man$|^hombre$")
    woman = _match_code(meta, "demo_gender", r"^woman$|^mujer$")
    identity_available = all(x is not None for x in (she, he, man, woman))
    identity_pair = (pronoun.eq(she) & gender.eq(man)) | (pronoun.eq(he) & gender.eq(woman)) if identity_available else pd.Series(False, index=df.index)
    identity_evaluable = pronoun.isin(_choices(meta.get("pronouns_elig", {}))) & gender.isin(_choices(meta.get("demo_gender", {}))) & identity_available
    out["Pronoun response"] = pronoun.map(_choices(meta.get("pronouns_elig", {}))).fillna("").astype(str)
    out["Gender response"] = gender.map(_choices(meta.get("demo_gender", {}))).fillna("").astype(str)
    out["She and man pairing"] = (pronoun.eq(she) & gender.eq(man)).astype(int)
    out["He and woman pairing"] = (pronoun.eq(he) & gender.eq(woman)).astype(int)
    rule("R14", identity_pair, identity_evaluable, out["Pronoun response"] + "; " + out["Gender response"] + "; context only, identity responses are not fraud evidence", absent=not identity_available)
    no_code = _match_code(meta, "fif_child_needs", r"^no$")
    explicit_no = _s(df, "fif_child_needs").map(_code).eq(no_code) if no_code is not None else pd.Series(False, index=df.index)
    needs_valid = _s(df, "fif_child_needs").map(_code).isin(_choices(meta.get("fif_child_needs", {})))
    out["Explicit no disability response"] = explicit_no.astype(int)
    rule("R15", explicit_no & autistic_low.ge(1), needs_valid & autistic_low.notna(), np.where(explicit_no & autistic_low.ge(1), "No disability + at least one autistic child; clarify wording/interpretation", "No observed disability/autism combination"), absent=no_code is None)

    # Evidence families are deliberately distinct from weighted prompt points.
    out["Timing evidence"] = (flags["R1"] | flags["R2"]).astype(int)
    out["Copying evidence"] = ((flags["R5"] & diverse) | flags["R7"]).astype(int)
    out["Logical evidence"] = count_conflict.astype(int)
    out["Attention evidence"] = attention_strong.astype(int)
    out["Independent evidence families"] = out[["Timing evidence", "Copying evidence", "Logical evidence", "Attention evidence"]].sum(axis=1)
    # Missing attention data is unassessable, not a positive flag. Missing all
    # survey timing is surfaced for review because absence is not a clean test.
    source_issue = (_s(df, "source_mapping_issue").str.lower().isin({"true", "1", "yes"})
                    | ~out["Source project verified"].eq("Yes"))
    out["Source mapping issue"] = source_issue.astype(int)
    out["Source mapping notes"] = _s(df, "source_mapping_notes")
    out["Review concern"] = (pd.DataFrame({r: flags[r] for r in ["R1", "R2", "R5", "R7", "R8", "R9", "R13", "R15"]}).any(axis=1)
                             | source_issue | out["Timing data issue"].eq(1)
                             | (completed & attention_available & ~answered)
                             | (completed & times.notna().sum(axis=1).eq(0))).astype(int)
    out["Payment contact hold"] = (~usable | disposable | confirmation_mismatch).astype(int)
    out["Responses sharing payment email"] = email.map(email[email.ne("")].value_counts()).fillna(0).astype(int)
    out.loc[out["Responses sharing payment email"].gt(1), "Payment contact hold"] = 1
    out["Risk score"] = sum(out[c] * r["points"] for c, r in RULES.items())
    out["Checks flagged"] = out[list(RULES)].sum(axis=1)
    out["Serious checks flagged (prompt weights)"] = out[[c for c, r in RULES.items() if r["points"] == 2]].sum(axis=1)
    out["Mild checks flagged (prompt weights)"] = out[[c for c, r in RULES.items() if r["points"] == 1]].sum(axis=1)
    out["Which checks were flagged"] = out[list(RULES)].apply(lambda r: "; ".join(c for c in r.index if r[c]), axis=1)
    out["Points from each check"] = out[list(RULES)].apply(lambda r: "; ".join(f"{c}={RULES[c]['points']}" for c in r.index if r[c]), axis=1)
    out["Rules evaluated"] = pd.DataFrame(evaluated).sum(axis=1)
    out["Score-only proposed action"] = np.where(~completed, "Incomplete - not eligible", np.where(out["Risk score"].ge(3), "Do not pay", np.where(out["Risk score"].ge(1), "Check by hand", "Pay now")))
    return _apply_decisions(out)


def _apply_decisions(out):
    out = out.copy()
    out["Recommended action"] = [decision_gate(r["Survey finished"] == "Yes", r["Independent evidence families"], r["Review concern"], r["Payment contact hold"], r["Source mapping issue"]) for _, r in out.iterrows()]
    reasons = []
    for _, r in out.iterrows():
        if r["Recommended action"] == "Incomplete - not eligible":
            reasons.append("At least one of the four required section completion statuses is missing or not complete.")
        elif r["Recommended action"] == "Do not pay":
            families = [c.removesuffix(" evidence").lower() for c in ["Timing evidence", "Copying evidence", "Logical evidence", "Attention evidence"] if r[c]]
            reasons.append("Hold payment: corroborating " + ", ".join(families) + " concerns. Automated integrity recommendation; bot identity is not independently confirmed.")
        elif r["Recommended action"] == "Check by hand":
            bits = []
            if r["Source mapping issue"]: bits.append("Resolve source or language mapping issue")
            if r["R1"] or r["R2"]: bits.append("Verify unusually short survey time")
            if r["R5"] or r["R7"]: bits.append("Compare matching response pattern or substantive comments")
            if r["R8"]: bits.append("Clarify family count or stored follow-up answers")
            if r["R9"]: bits.append("Verify age or postal information")
            if r["R13"]: bits.append("Review Band-Aid answer or reported image issue")
            if r["R15"]: bits.append("Clarify disability wording versus autistic-child count")
            if r["Attention check available"] and not r["Attention check answered"]:
                bits.append("Attention question unanswered")
            if r["Timing data issue"] or r["Sections with a recorded time"] == 0:
                bits.append("Check missing or invalid section times")
            if r["Email usable"] != "Yes": bits.append("Obtain usable payment email")
            if r["R10"]: bits.append("Verify temporary email contact")
            if r["Email confirmation differs"]: bits.append("Resolve email confirmation mismatch")
            if r["Responses sharing payment email"] > 1: bits.append("Check shared payment email before duplicate payment")
            reasons.append("; ".join(bits) + ".")
        else:
            reasons.append("All four sections finished, usable unique payment contact, no substantive unresolved concern. Context-only checks do not establish fraud.")
    out["Why this action"] = reasons
    out["Review priority"] = np.where(out["Recommended action"].ne("Check by hand"), "",
        np.where(out["Source mapping issue"].eq(1), "Resolve source mapping first",
        np.where(out["Independent evidence families"].ge(1), "1 - Integrity evidence",
        np.where(out["Review concern"].eq(1), "2 - Response clarification", "3 - Payment contact"))))
    out["Review plan"] = np.where(out["Recommended action"].eq("Check by hand"), out["Why this action"], "")
    out["Final plan"] = out["Recommended action"]
    out["Bot assessment"] = np.where(out["Recommended action"].eq("Do not pay"), "Multiple independent integrity concerns; not confirmed", np.where(out["Recommended action"].eq("Pay now"), "No substantive integrity concern detected; identity not independently verified", "Unresolved / insufficient evidence"))
    return out


def score_refined_records(sources):
    frames = []
    for pid, records in sources.records_by_pid.items():
        study = sources.registry[pid]
        frame = score_study(records, study["display_label"], sources.metadata_by_pid[pid], project_id=pid, provenance_verified=True)
        frame["Study name"] = study["study_name"]
        frame["Recruitment population"] = study["population"]
        frame["Reference cohort designation"] = study.get("reference_validity", "")
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True)
    if not out["Response key"].is_unique:
        raise ValueError("Project/record composite key is not unique")
    email = out["Email address"]
    out["Responses sharing payment email"] = email.map(email[email.ne("")].value_counts()).fillna(0).astype(int)
    out.loc[out["Responses sharing payment email"].gt(1), "Payment contact hold"] = 1
    return _apply_decisions(out)


def rule_catalog():
    return pd.DataFrame([{"Rule": code, "Check": rule["label"], "Prompt points": rule["points"], "Evidence family": rule["family"], "Definition and interpretation": rule["description"]} for code, rule in RULES.items()])
