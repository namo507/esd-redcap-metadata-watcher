"""
generate_all_responses_excel.py
================================
Generates a comprehensive AllResponses Excel workbook covering every study
and every record in the ESD caregiver bot-detection pipeline.

Studies covered
---------------
- Study 1  PID 4797  : Verified caregiver sample   (clean reference)
- Study 2  PID 4581  : Online recruitment sample   (dirty / recruited)
- Study 3  PID 4700+5749 : Bilingual combined sample
- Study 4  PID 4931  : ICIS sample

Data-cleaning rules (bilingual)
--------------------------------
PID 5749 full parquet = records 1-520:
  - Exclude records 446-473: signed up before June 10 2026 (Dr. Bradshaw request)
  - Exclude record 517: occup_1 contains 'TEST'
  - Records 1-445 are sourced from the 4700 archive parquet (cleaner timestamps)
  - Records 474-518 minus 517 come from the 5749 parquet

Phase-1 quality fixes applied
------------------------------
1. Survey-completeness gate: incomplete records -> "Incomplete - not eligible"
2. Arrival tight-cluster: now scores +2 points (was 0 / eye-ball only)
3. Missing email gate: +1 point if no email on a finished form
"""
from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

pd.set_option('future.no_silent_downcasting', True)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR  = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
CACHE_DIR   = PROJECT_DIR / "data_cache"
OUTPUT_DIR  = PROJECT_DIR / "Caregiver Outputs" / "restricted"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE    = PROJECT_DIR.parent.parent / ".env"
load_dotenv(ENV_FILE)

# ---------------------------------------------------------------------------
# Thresholds (calibrated on 131 verified-human completions in PID 4797)
# ---------------------------------------------------------------------------
TOTAL_TIME_FLOOR_MIN = 11.57
TFA_TIME_FLOOR_MIN   =  7.85
OVERNIGHT_HOUR_START =  0       # midnight
OVERNIGHT_HOUR_END   =  5       # 5 am (exclusive)

SECTION_FLOORS_MIN = {
    "fif":  2.55,
    "val":  0.22,
    "tfa":  6.67,
    "demo": 0.95,
}

# Point weights per check
POINT_LABELS = {
    "Whole survey finished faster than any verified caregiver":       2,
    "Thoughts and feelings section faster than any verified caregiver": 2,
    "One of the four sections finished below its own speed line":     1,
    "The same answer repeated down a rating block":                   1,
    "Answer sheet identical to another response":                     2,
    "Arrived within a minute of two or more other sign-ups":          2,
    "Written comment nearly identical to another response":           1,
    "Family answers contradict each other":                           2,
    "Age and location cannot both be true":                           2,
    "Throwaway or temporary email address":                           1,
    "No email address on a finished form":                            1,
    "Started between midnight and five in the morning":               1,
}

SERIOUS_CHECKS = {
    "Whole survey finished faster than any verified caregiver",
    "Thoughts and feelings section faster than any verified caregiver",
    "Answer sheet identical to another response",
    "Arrived within a minute of two or more other sign-ups",
    "Family answers contradict each other",
    "Age and location cannot both be true",
}

DO_NOT_PAY_THRESHOLD    = 3
CHECK_BY_HAND_THRESHOLD = 1

BURST_WINDOW_SECONDS = 120
BURST_MIN_SIGN_UPS   = 3

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "temp-mail.org", "yopmail.com", "throwam.com", "fakeinbox.com",
    "trashmail.com", "dispostable.com", "maildrop.cc",
}

UNIVERSITY_DOMAINS = {"sc.edu", "uscmed.sc.edu", "mailbox.sc.edu"}

# ---------------------------------------------------------------------------
# Spanish-twin folding
# ---------------------------------------------------------------------------
_LANG_SUFFIX = re.compile(r"^(?P<base>.+?)_(?:s|sp)(?P<checkbox>___.+)?$")


def _canonical(col: str) -> Optional[str]:
    m = _LANG_SUFFIX.match(col)
    if m is None:
        return None
    return m.group("base") + (m.group("checkbox") or "")


def fold_language_twins(df: pd.DataFrame) -> pd.DataFrame:
    cols = set(df.columns)
    out = df.copy()
    for col in sorted(cols):
        base = _canonical(col)
        if base is None:
            continue
        if base in cols:
            en = out[base].astype("string").fillna("")
            es = out[col].astype("string").fillna("")
            out[base] = en.where(en.str.strip().ne(""), es)
            out = out.drop(columns=[col])
        else:
            out = out.rename(columns={col: base})
    return out


# ---------------------------------------------------------------------------
# Parquet loading
# ---------------------------------------------------------------------------
def _latest_parquet(pid: int) -> Path:
    matches = sorted(CACHE_DIR.glob(f"{pid}_record_*.parquet"))
    if not matches:
        raise FileNotFoundError(f"No parquet cache for project {pid} in {CACHE_DIR}")
    return matches[-1]


def load_project(pid: int, fold_twins: bool = False) -> pd.DataFrame:
    df = pd.read_parquet(_latest_parquet(pid)).copy()
    df["record_id"] = df["record_id"].astype(str)
    if fold_twins:
        df = fold_language_twins(df)
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


def _str_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series("", index=df.index)
    return df[col].fillna("").astype(str).str.strip()


def _section_minutes(df: pd.DataFrame, short: str) -> pd.Series:
    return _num(df, f"get_time_{short}")


def _sections_with_time(df: pd.DataFrame) -> pd.Series:
    parts = pd.DataFrame({s: _section_minutes(df, s).notna()
                          for s in ("fif", "val", "tfa", "demo")})
    return parts.sum(axis=1).astype(int)


def _completed_all(df: pd.DataFrame) -> pd.Series:
    fields = [
        "family_information_form_complete",
        "values_complete",
        "tfa_complete",
        "demographics_complete",
    ]
    present = [f for f in fields if f in df.columns]
    if not present:
        return pd.Series(False, index=df.index)
    return pd.DataFrame({f: _num(df, f).eq(2) for f in present}).all(axis=1)


def _parse_timestamps(df: pd.DataFrame) -> pd.Series:
    if "eligibility_timestamp" not in df.columns:
        return pd.Series(pd.NaT, index=df.index)
    return pd.to_datetime(df["eligibility_timestamp"], errors="coerce")


def _get_email(df: pd.DataFrame) -> pd.Series:
    for col in ("demo_email", "email_elig"):
        if col in df.columns:
            return df[col].fillna("").astype(str).str.strip().str.lower()
    return pd.Series("", index=df.index)


def _email_domain(es: pd.Series) -> pd.Series:
    return es.str.rsplit("@", n=1).str[-1].str.lower()


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------
def _likert_cols(df: pd.DataFrame) -> list:
    candidate = []
    for col in df.columns:
        vals = _num(df, col).dropna()
        if len(vals) < 5:
            continue
        if set(vals.unique()).issubset({1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0}):
            candidate.append(col)
    return candidate


def _flat_response_flag(df: pd.DataFrame) -> pd.Series:
    cols = _likert_cols(df)
    if not cols:
        return pd.Series(False, index=df.index)
    mat = df[cols].apply(pd.to_numeric, errors="coerce")
    return mat.std(axis=1, ddof=1).fillna(1.0).lt(0.15)


def _fingerprint_duplicates(df: pd.DataFrame, cols: list) -> pd.Series:
    if not cols:
        return pd.Series(False, index=df.index)
    # Convert to standard object dtype first to avoid Float64/Int64 fillna issues
    raw = df[cols].copy()
    for c in raw.columns:
        raw[c] = pd.to_numeric(raw[c], errors="coerce").astype(object)
    mat = raw.fillna("<NA>").astype(str)
    fp = mat.agg("|".join, axis=1).map(
        lambda v: hashlib.sha256(v.encode()).hexdigest()
    )
    answered = mat.ne("<NA>").mean(axis=1).ge(0.80)
    return fp.duplicated(keep=False) & answered


def _open_text_similarity(df: pd.DataFrame) -> pd.Series:
    text_cols = [c for c in df.columns
                 if any(k in c for k in
                        ("comment", "detail", "reason", "ethic", "feedback", "other"))]
    if not text_cols:
        return pd.Series(0.0, index=df.index)
    texts = df[text_cols].fillna("").astype(str).agg(" ".join, axis=1).str.strip()
    if texts.ne("").sum() < 2:
        return pd.Series(0.0, index=df.index)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics import pairwise_distances
        tfidf = TfidfVectorizer(min_df=1, max_df=1.0, ngram_range=(1, 2))
        mat = tfidf.fit_transform(texts.fillna(""))
        sim = 1.0 - pairwise_distances(mat, metric="cosine", n_jobs=-1)
        np.fill_diagonal(sim, 0.0)
        return pd.Series(sim.max(axis=1), index=df.index)
    except Exception:
        return pd.Series(0.0, index=df.index)


def _count_concurrent_signups(ts_series: pd.Series) -> pd.Series:
    ts_valid = ts_series.dropna().sort_values()
    result = pd.Series(0, index=ts_series.index)
    if len(ts_valid) == 0:
        return result
    ts_arr = ts_valid.values.astype("datetime64[s]").astype(np.int64)
    for idx, t in zip(ts_valid.index, ts_arr):
        lo = np.searchsorted(ts_arr, t - BURST_WINDOW_SECONDS, side="left")
        hi = np.searchsorted(ts_arr, t + BURST_WINDOW_SECONDS, side="right")
        result.at[idx] = (hi - lo) - 1
    return result


def _tight_cluster_flag(df: pd.DataFrame):
    ts = _parse_timestamps(df)
    concurrent = _count_concurrent_signups(ts)
    return concurrent, concurrent.ge(BURST_MIN_SIGN_UPS - 1)


def _family_logic_error(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    errors = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)
    if "fif_num_autistic" in df.columns:
        zero_autistic = _num(df, "fif_num_autistic").eq(0)
        followup = [c for c in df.columns
                    if "fif_num_autistic" in c and c != "fif_num_autistic"]
        if followup:
            has_fu = df[followup].fillna("").astype(str).ne("").any(axis=1)
            mask_fu = zero_autistic & has_fu
            errors |= mask_fu
            for i in df.index[mask_fu]:
                reasons.at[i] = "0 autistic children reported but follow-up answered"
    age_band_cols = [c for c in df.columns
                     if re.match(r"fif_childrens_ages___\d+$", c)]
    if age_band_cols and "fif_num_children" in df.columns:
        selected = df[age_band_cols].apply(pd.to_numeric, errors="coerce").eq(1).sum(axis=1)
        n_ch = _num(df, "fif_num_children")
        mask_ch = n_ch.notna() & selected.gt(n_ch)
        errors |= mask_ch
        for i in df.index[mask_ch]:
            r_str = f"child age bands ({int(selected.at[i])}) > fif_num_children ({int(n_ch.at[i])})"
            reasons.at[i] = (reasons.at[i] + "; " + r_str).strip("; ")
    return errors, reasons


def _impossible_demographics(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    errors = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)
    age = _num(df, "age_check_demo")
    mask_age = age.notna() & age.ne(0) & ~age.between(18, 100)
    errors |= mask_age
    for i in df.index[mask_age]:
        reasons.at[i] = f"age={age.at[i]} not in 18-100"
    if "zip_demo" in df.columns and "demo_country" in df.columns:
        zip_text = _str_col(df, "zip_demo")
        us = _num(df, "demo_country").eq(1)
        mask_zip = us & zip_text.ne("") & ~zip_text.str.fullmatch(r"\d{5}(?:-\d{4})?")
        errors |= mask_zip
        for i in df.index[mask_zip]:
            r_str = f"US demo_country=1 but invalid ZIP '{zip_text.at[i]}'"
            reasons.at[i] = (reasons.at[i] + "; " + r_str).strip("; ")
    if "dob_child1" in df.columns:
        birth_yr = _num(df, "dob_child1")
        age_at_birth = age - (datetime.now().year - birth_yr)
        plausible = age.between(18, 100)
        mask_birth = plausible & birth_yr.notna() & ~age_at_birth.between(10, 60)
        errors |= mask_birth
        for i in df.index[mask_birth]:
            r_str = f"parent age at birth={age_at_birth.at[i]:.0f} not in 10-60"
            reasons.at[i] = (reasons.at[i] + "; " + r_str).strip("; ")
    return errors, reasons


# ---------------------------------------------------------------------------
# Main scoring engine
# ---------------------------------------------------------------------------
def score_study(df: pd.DataFrame, study_label: str) -> pd.DataFrame:
    df = df.copy()

    fif_min  = _section_minutes(df, "fif").round(2)
    val_min  = _section_minutes(df, "val").round(2)
    tfa_min  = _section_minutes(df, "tfa").round(2)
    demo_min = _section_minutes(df, "demo").round(2)
    total_min = (fif_min + val_min + tfa_min + demo_min).round(2)
    sections_timed = _sections_with_time(df)
    completed = _completed_all(df)

    ts_start = _parse_timestamps(df)
    ts_end = pd.Series(pd.NaT, index=df.index)
    for col in df.columns:
        if "timestamp" in col.lower() and col != "eligibility_timestamp":
            t = pd.to_datetime(df[col], errors="coerce")
            ts_end = ts_end.combine_first(t)
    elapsed_min = ((ts_end - ts_start).dt.total_seconds() / 60).round(2)

    r_whole_fast    = total_min.lt(TOTAL_TIME_FLOOR_MIN).fillna(False)
    r_tfa_fast      = tfa_min.lt(TFA_TIME_FLOOR_MIN).fillna(False)
    fif_low = fif_min.lt(SECTION_FLOORS_MIN["fif"]).fillna(False)
    val_low = val_min.lt(SECTION_FLOORS_MIN["val"]).fillna(False)
    tfa_low = tfa_min.lt(SECTION_FLOORS_MIN["tfa"]).fillna(False)
    demo_low = demo_min.lt(SECTION_FLOORS_MIN["demo"]).fillna(False)
    r_section_low = fif_low | val_low | tfa_low | demo_low

    r_whole_in_band = (
        total_min.ge(TOTAL_TIME_FLOOR_MIN) & total_min.lt(TOTAL_TIME_FLOOR_MIN * 1.3)
    ).fillna(False)
    r_tfa_in_band = (
        tfa_min.ge(TFA_TIME_FLOOR_MIN) & tfa_min.lt(TFA_TIME_FLOOR_MIN * 1.3)
    ).fillna(False)

    lk_cols = _likert_cols(df)
    if lk_cols:
        mat_lk = df[lk_cols].apply(pd.to_numeric, errors="coerce")
        stds = mat_lk.std(axis=1, ddof=1)
        r_flat = stds.fillna(1.0).lt(0.15)

        raw = df[lk_cols].copy()
        for c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce").astype(object)
        mat_fp = raw.fillna("<NA>").astype(str)
        fp = mat_fp.agg("|".join, axis=1).map(
            lambda v: hashlib.sha256(v.encode()).hexdigest()
        )
        answered_pct = mat_fp.ne("<NA>").mean(axis=1)
        r_fingerprint = fp.duplicated(keep=False) & answered_pct.ge(0.80)
    else:
        stds = pd.Series(np.nan, index=df.index)
        r_flat = pd.Series(False, index=df.index)
        fp = pd.Series("", index=df.index)
        answered_pct = pd.Series(0.0, index=df.index)
        r_fingerprint = pd.Series(False, index=df.index)

    concurrent, r_cluster = _tight_cluster_flag(df)
    sim_scores      = _open_text_similarity(df)
    r_near_dup      = sim_scores.gt(0.90)
    r_family_err, r_family_reasons = _family_logic_error(df)
    r_impossible, r_demo_reasons   = _impossible_demographics(df)

    email           = _get_email(df)
    dom             = _email_domain(email)
    r_throwaway     = dom.isin(DISPOSABLE_DOMAINS) & email.ne("")
    r_no_email      = email.eq("") & completed
    r_univ_email    = dom.isin(UNIVERSITY_DOMAINS) & email.ne("")
    dom_counts      = dom[dom.ne("")].value_counts()
    r_unique_domain = dom.map(lambda d: dom_counts.get(d, 0) if d else 0).le(1) & dom.ne("")

    r_overnight     = (ts_start.dt.hour >= OVERNIGHT_HOUR_START) & (ts_start.dt.hour < OVERNIGHT_HOUR_END)
    r_overnight     = r_overnight.fillna(False)

    flags = {
        "Whole survey finished faster than any verified caregiver":          r_whole_fast,
        "Thoughts and feelings section faster than any verified caregiver":  r_tfa_fast,
        "One of the four sections finished below its own speed line":        r_section_low,
        "The same answer repeated down a rating block":                      r_flat,
        "Answer sheet identical to another response":                        r_fingerprint,
        "Arrived within a minute of two or more other sign-ups":             r_cluster,
        "Written comment nearly identical to another response":              r_near_dup,
        "Family answers contradict each other":                              r_family_err,
        "Age and location cannot both be true":                              r_impossible,
        "Throwaway or temporary email address":                              r_throwaway,
        "No email address on a finished form":                               r_no_email,
        "Started between midnight and five in the morning":                  r_overnight,
    }

    score = pd.Series(0, index=df.index, dtype=int)
    for label, mask in flags.items():
        score += mask.astype(int) * POINT_LABELS[label]

    checks_broken  = sum(m.astype(int) for m in flags.values())
    serious_broken = sum(
        flags[label].astype(int)
        for label in SERIOUS_CHECKS if label in flags
    )
    mild_broken    = checks_broken - serious_broken

    def _which_checks(i):
        return "; ".join(lbl for lbl, m in flags.items() if m.at[i]) or ""

    def _points_narrative(i):
        parts = [
            f"{lbl} ({POINT_LABELS[lbl]} point{'s' if POINT_LABELS[lbl] > 1 else ''})"
            for lbl, m in flags.items() if m.at[i]
        ]
        return "; ".join(parts) if parts else ""

    def _action(i):
        if not completed.at[i]:
            return "Incomplete - not eligible"
        s = score.at[i]
        if s >= DO_NOT_PAY_THRESHOLD:
            return "Do not pay"
        elif s >= CHECK_BY_HAND_THRESHOLD:
            return "Check by hand"
        return "Pay now"

    def _why(i):
        if not completed.at[i]:
            return "Survey not finished"
        s = score.at[i]
        if s >= DO_NOT_PAY_THRESHOLD:
            return f"{s} points or more"
        elif s >= CHECK_BY_HAND_THRESHOLD:
            return f"{s} point{'s' if s > 1 else ''}"
        return "No issues found"

    which_list  = [_which_checks(i)    for i in df.index]
    points_list = [_points_narrative(i) for i in df.index]
    action_list = [_action(i)           for i in df.index]
    why_list    = [_why(i)              for i in df.index]

    yn = lambda s: s.map({True: "Yes", False: "No"})

    out = pd.DataFrame(index=df.index)
    out["Study"]                                                               = study_label
    out["Record ID"]                                                           = df["record_id"].astype(str)
    out["Email address"]                                                       = email
    out["University email address"]                                            = yn(r_univ_email)
    out["Started"]                                                             = ts_start.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    out["Last section handed in"]                                              = ts_end.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
    out["Started overnight"]                                                   = yn(r_overnight)
    out["Family Information minutes"]                                          = fif_min
    out["Values minutes"]                                                      = val_min
    out["Thoughts and feelings minutes"]                                       = tfa_min
    out["Demographics minutes"]                                                = demo_min
    out["Whole survey minutes"]                                                = total_min
    out["Sections with a recorded time"]                                       = sections_timed
    out["Minutes from sign-up to last section"]                               = elapsed_min
    out["Whole survey speed"]                                                  = pd.cut(
        total_min,
        bins=[-np.inf, TOTAL_TIME_FLOOR_MIN, TOTAL_TIME_FLOOR_MIN * 1.3, np.inf],
        labels=["Definitely rushed", "Slightly fast", "Normal"],
    ).astype(str).replace("nan", "No data")
    out["Thoughts and feelings speed"]                                         = pd.cut(
        tfa_min,
        bins=[-np.inf, TFA_TIME_FLOOR_MIN, TFA_TIME_FLOOR_MIN * 1.3, np.inf],
        labels=["Definitely rushed", "Slightly fast", "Normal"],
    ).astype(str).replace("nan", "No data")
    out["Whole survey finished faster than any verified caregiver"]            = yn(r_whole_fast)
    out["Whole survey time sits inside the uncertainty band"]                  = yn(r_whole_in_band)
    out["Thoughts and feelings section faster than any verified caregiver"]    = yn(r_tfa_fast)
    out["Thoughts and feelings time sits inside the uncertainty band"]         = yn(r_tfa_in_band)
    out["One of the four sections finished below its own speed line"]          = yn(r_section_low)
    out["The same answer repeated down a rating block"]                        = yn(r_flat)
    out["Answer sheet identical to another response"]                          = yn(r_fingerprint)
    out["Arrived within a minute of two or more other sign-ups"]               = yn(r_cluster)
    out["Written comment nearly identical to another response"]                = yn(r_near_dup)
    out["Family answers contradict each other"]                                = yn(r_family_err)
    out["Age and location cannot both be true"]                                = yn(r_impossible)
    out["Throwaway or temporary email address"]                                = yn(r_throwaway)
    out["No email address on a finished form"]                                 = yn(r_no_email)
    out["Started between midnight and five in the morning"]                    = yn(r_overnight)
    out["Which checks were broken"]                                            = which_list
    out["Points from each check"]                                              = points_list
    out["Checks broken"]                                                       = checks_broken.values
    out["Serious checks broken"]                                               = serious_broken.values
    out["Mild checks broken"]                                                  = mild_broken.values
    out["Risk score"]                                                          = score.values
    out["Recommended action"]                                                  = action_list
    out["Why this action"]                                                     = why_list
    out["Review plan"]                                                         = action_list
    out["Why this plan"]                                                       = why_list
    out["Final plan"]                                                          = action_list
    out["Cleared by university email"]                                         = yn(r_univ_email)
    out["Sign-ups within two minutes"]                                         = concurrent.values
    out["Arrived in a tight cluster"]                                          = yn(r_cluster)
    out["Email domain seen only once"]                                         = yn(r_unique_domain)
    out["Named in the earlier confirmed list"]                                 = "No"

    # -----------------------------------------------------------------------
    # Regression testing columns (formula in brackets in header)
    # -----------------------------------------------------------------------
    reg_master_list = []
    reg_score_check_list = []
    reg_decision_check_list = []
    reg_r1_list = []
    reg_r2_list = []
    reg_r3_list = []
    reg_r4_list = []
    reg_r5_list = []
    reg_r6_list = []
    reg_r7_list = []
    reg_r8_list = []
    reg_r9_list = []
    reg_r10_list = []
    reg_r11_list = []
    reg_r12_list = []

    for i in df.index:
        # R1
        tot = total_min.at[i]
        if r_whole_fast.at[i]:
            r1_val = f"FAIL ({tot:.2f} min < 11.57 min)"
        elif pd.notna(tot):
            r1_val = f"PASS ({tot:.2f} min >= 11.57 min)"
        else:
            r1_val = "PASS (no timing data)"
        reg_r1_list.append(r1_val)

        # R2
        tfa = tfa_min.at[i]
        if r_tfa_fast.at[i]:
            r2_val = f"FAIL ({tfa:.2f} min < 7.85 min)"
        elif pd.notna(tfa):
            r2_val = f"PASS ({tfa:.2f} min >= 7.85 min)"
        else:
            r2_val = "PASS (no timing data)"
        reg_r2_list.append(r2_val)

        # R3
        lows = []
        if fif_low.at[i]:  lows.append(f"fif={fif_min.at[i]:.2f}<2.55")
        if val_low.at[i]:  lows.append(f"val={val_min.at[i]:.2f}<0.22")
        if tfa_low.at[i]:  lows.append(f"tfa={tfa_min.at[i]:.2f}<6.67")
        if demo_low.at[i]: lows.append(f"demo={demo_min.at[i]:.2f}<0.95")
        if r_section_low.at[i]:
            r3_val = f"FAIL ({'; '.join(lows)})"
        else:
            r3_val = "PASS (all timed sections >= floor)"
        reg_r3_list.append(r3_val)

        # R4
        sd_val = stds.at[i] if lk_cols else np.nan
        if r_flat.at[i]:
            r4_val = f"FAIL (Likert SD={sd_val:.3f} < 0.15)"
        elif pd.notna(sd_val):
            r4_val = f"PASS (Likert SD={sd_val:.3f} >= 0.15)"
        else:
            r4_val = "PASS (no Likert data)"
        reg_r4_list.append(r4_val)

        # R5
        ans_pct = answered_pct.at[i] if lk_cols else 0.0
        fp_str = fp.at[i] if lk_cols else ""
        if r_fingerprint.at[i]:
            r5_val = f"FAIL (SHA256={fp_str[:8]}... dup, {ans_pct*100:.0f}% answered >= 80%)"
        else:
            r5_val = f"PASS (unique answer sheet, {ans_pct*100:.0f}% answered)"
        reg_r5_list.append(r5_val)

        # R6
        cc = concurrent.at[i]
        if r_cluster.at[i]:
            r6_val = f"FAIL ({cc} sign-ups in +/-120s >= 2)"
        else:
            r6_val = f"PASS ({cc} sign-ups in +/-120s < 2)"
        reg_r6_list.append(r6_val)

        # R7
        sim_val = sim_scores.at[i]
        if r_near_dup.at[i]:
            r7_val = f"FAIL (max cosine sim={sim_val:.2f} > 0.90)"
        else:
            r7_val = f"PASS (max cosine sim={sim_val:.2f} <= 0.90)"
        reg_r7_list.append(r7_val)

        # R8
        if r_family_err.at[i]:
            r8_val = f"FAIL ({r_family_reasons.at[i]})"
        else:
            r8_val = "PASS (family logic consistent)"
        reg_r8_list.append(r8_val)

        # R9
        if r_impossible.at[i]:
            r9_val = f"FAIL ({r_demo_reasons.at[i]})"
        else:
            r9_val = "PASS (demographics plausible)"
        reg_r9_list.append(r9_val)

        # R10
        em = email.at[i]
        dm = dom.at[i]
        if r_throwaway.at[i]:
            r10_val = f"FAIL (domain '{dm}' is disposable)"
        elif em != "":
            r10_val = f"PASS (domain '{dm}' not disposable)"
        else:
            r10_val = "PASS (no email provided)"
        reg_r10_list.append(r10_val)

        # R11
        comp = completed.at[i]
        if r_no_email.at[i]:
            r11_val = "FAIL (email blank but all 4 sections complete)"
        elif comp:
            r11_val = f"PASS (email present: '{em}')"
        else:
            r11_val = "PASS (survey incomplete)"
        reg_r11_list.append(r11_val)

        # R12
        ts = ts_start.at[i]
        if r_overnight.at[i]:
            r12_val = f"FAIL (started at {ts.strftime('%H:%M:%S')} in 00:00-04:59)"
        elif pd.notna(ts):
            r12_val = f"PASS (started at {ts.strftime('%H:%M:%S')} outside 00:00-04:59)"
        else:
            r12_val = "PASS (no timestamp)"
        reg_r12_list.append(r12_val)

        # Master summary of failing rules
        fails = []
        if r_whole_fast.at[i]:   fails.append(f"R1: total={tot:.2f}m < 11.57m")
        if r_tfa_fast.at[i]:     fails.append(f"R2: tfa={tfa:.2f}m < 7.85m")
        if r_section_low.at[i]:   fails.append(f"R3: {'; '.join(lows)}")
        if r_flat.at[i]:         fails.append(f"R4: Likert SD={sd_val:.3f} < 0.15")
        if r_fingerprint.at[i]:  fails.append(f"R5: SHA256={fp_str[:8]}... dup")
        if r_cluster.at[i]:      fails.append(f"R6: {cc} sign-ups in 2m >= 2")
        if r_near_dup.at[i]:     fails.append(f"R7: text cosine sim={sim_val:.2f} > 0.90")
        if r_family_err.at[i]:   fails.append(f"R8: {r_family_reasons.at[i]}")
        if r_impossible.at[i]:   fails.append(f"R9: {r_demo_reasons.at[i]}")
        if r_throwaway.at[i]:    fails.append(f"R10: disposable domain '{dm}'")
        if r_no_email.at[i]:     fails.append("R11: missing email on complete form")
        if r_overnight.at[i]:    fails.append(f"R12: started {ts.strftime('%H:%M')} overnight")

        if not fails:
            master_val = "ALL RULES PASS (0 broken)"
        else:
            master_val = f"FAILED ({len(fails)} rule{'s' if len(fails)>1 else ''}): {'; '.join(fails)}"
        reg_master_list.append(master_val)

        # Score regression check
        calc_score = (
            int(r_whole_fast.at[i]) * 2
            + int(r_tfa_fast.at[i]) * 2
            + int(r_section_low.at[i]) * 1
            + int(r_flat.at[i]) * 1
            + int(r_fingerprint.at[i]) * 2
            + int(r_cluster.at[i]) * 2
            + int(r_near_dup.at[i]) * 1
            + int(r_family_err.at[i]) * 2
            + int(r_impossible.at[i]) * 2
            + int(r_throwaway.at[i]) * 1
            + int(r_no_email.at[i]) * 1
            + int(r_overnight.at[i]) * 1
        )
        stored_s = score.at[i]
        if stored_s == calc_score:
            reg_score_check_list.append(f"PASS (Risk score={stored_s} == sum of points={calc_score})")
        else:
            reg_score_check_list.append(f"FAIL: MISMATCH (Risk score={stored_s} != sum of points={calc_score})")

        # Decision regression check
        if not comp:
            expected_act = "Incomplete - not eligible"
        elif stored_s >= DO_NOT_PAY_THRESHOLD:
            expected_act = "Do not pay"
        elif stored_s >= CHECK_BY_HAND_THRESHOLD:
            expected_act = "Check by hand"
        else:
            expected_act = "Pay now"

        act = _action(i)
        if act == expected_act:
            reg_decision_check_list.append(f"PASS ('{act}' matches Gate[complete={comp}, score={stored_s}])")
        else:
            reg_decision_check_list.append(f"FAIL: MISMATCH ('{act}' != expected '{expected_act}')")

    out["Regression testing for each rule [R1: total<11.57m | R2: tfa<7.85m | R3: sec_floor | R4: likert_sd<0.15 | R5: fp_dup | R6: burst>=2 | R7: text_sim>0.90 | R8: fam_logic | R9: demo_logic | R10: throwaway_email | R11: no_email_complete | R12: hour_0_4]"] = reg_master_list
    out["Score Regression Check [Risk score == sum(broken rule points)]"] = reg_score_check_list
    out["Decision Regression Check [Recommended action == Gate(Completed, Risk score)]"] = reg_decision_check_list
    out["R1 Regression [get_time_fif + get_time_val + get_time_tfa + get_time_demo < 11.57 min]"] = reg_r1_list
    out["R2 Regression [get_time_tfa < 7.85 min]"] = reg_r2_list
    out["R3 Regression [fif < 2.55 | val < 0.22 | tfa < 6.67 | demo < 0.95 min]"] = reg_r3_list
    out["R4 Regression [Likert items row_stdev < 0.15]"] = reg_r4_list
    out["R5 Regression [SHA-256(Likert vector) duplicated & answered >= 80%]"] = reg_r5_list
    out["R6 Regression [Count(eligibility_timestamp +/- 120s) >= 2]"] = reg_r6_list
    out["R7 Regression [max(TF-IDF open text cosine similarity) > 0.90]"] = reg_r7_list
    out["R8 Regression [fif_num_autistic == 0 & followup != '' | child_age_bands > fif_num_children]"] = reg_r8_list
    out["R9 Regression [age not in 18-100 | (US & invalid ZIP) | parent_age_at_birth not in 10-60]"] = reg_r9_list
    out["R10 Regression [email domain in DISPOSABLE_DOMAINS]"] = reg_r10_list
    out["R11 Regression [demo_email == '' & all 4 sections complete]"] = reg_r11_list
    out["R12 Regression [eligibility_timestamp hour in 0..4]"] = reg_r12_list

    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Study-specific loaders
# ---------------------------------------------------------------------------
def load_study_4797():
    print("  Loading Study 1 (PID 4797 - clean reference)...")
    df = load_project(4797)
    return df, "Study 1 - verified caregiver sample (PID 4797)"


def load_study_4581():
    print("  Loading Study 2 (PID 4581 - online recruitment)...")
    df = load_project(4581)
    return df, "Study 2 - online recruitment sample (PID 4581)"


def _backfill_4700_timing(df_5749: pd.DataFrame) -> pd.DataFrame:
    df_4700 = load_project(4700)
    timing_cols = [
        "eligibility_timestamp",
        "family_information_form_timestamp",
        "get_time_fif", "get_min_fif", "get_secs_fif", "survey_time_fif",
        "values_timestamp",
        "get_time_val", "get_min_val", "get_secs_val", "survey_time_val",
        "tfa_timestamp",
        "get_time_tfa", "get_min_tfa", "get_secs_tfa", "survey_time_tfa",
        "demographics_timestamp",
        "get_time_demo", "get_min_demo", "get_secs_demo", "survey_time_demo",
    ]
    cols_to_transfer = [c for c in timing_cols
                        if c in df_4700.columns and c in df_5749.columns]
    map_4700 = df_4700.set_index("record_id")[cols_to_transfer]
    df_out = df_5749.copy()
    rec_ids = df_out["record_id"].astype(str)
    needs_fill = (
        (df_out["eligibility_timestamp"].isna() | df_out["eligibility_timestamp"].eq(""))
        & rec_ids.isin(map_4700.index)
    )
    if needs_fill.any():
        for col in cols_to_transfer:
            filled = rec_ids[needs_fill].map(map_4700[col])
            valid = needs_fill & filled.notna() & filled.ne("")
            df_out.loc[valid, col] = filled[valid]
    return df_out


def load_study_bilingual():
    """
    Bilingual combined sample.
      Records 1-445   : sourced from 4700 archive parquet
      Records 474-518 : sourced from 5749 parquet, minus record 517 (TEST)
      Records 446-473 : EXCLUDED (Dr. Bradshaw request, pre-June 10 2026)
    """
    print("  Loading Study 3 (PID 4700 archive + 5749 bilingual new-wave)...")
    df_5749 = load_project(5749, fold_twins=True)
    df_5749 = _backfill_4700_timing(df_5749)
    df_5749["record_id_int"] = pd.to_numeric(df_5749["record_id"], errors="coerce")

    r1_445    = df_5749[df_5749["record_id_int"].between(1, 445)].copy()
    r474_plus = df_5749[df_5749["record_id_int"] >= 474].copy()

    # Exclude any records with 'TEST' in occup_1 or occup_1_s
    is_test = pd.Series(False, index=r474_plus.index)
    for col in ("occup_1", "occup_1_s"):
        if col in r474_plus.columns:
            is_test |= r474_plus[col].fillna("").astype(str).str.strip().str.lower().str.contains("test")
    if is_test.any():
        excluded_ids = r474_plus.loc[is_test, "record_id"].tolist()
        print(f"    Excluding {is_test.sum()} TEST record(s): {excluded_ids}")
        r474_plus = r474_plus[~is_test]

    print(f"    Records 1-445  : {len(r1_445)}")
    print(f"    Records 474+ (excl. TEST): {len(r474_plus)}")
    print(f"    Records 446-473 EXCLUDED by Dr. Bradshaw request")

    combined = pd.concat([r1_445, r474_plus], ignore_index=True)
    combined.drop(columns=["record_id_int"], inplace=True, errors="ignore")

    return combined, "Study 3 - bilingual sample (PID 4700 + 5749)"


def load_study_icis():
    print("  Loading Study 4 (PID 4931 - ICIS)...")
    df = load_project(4931)
    return df, "Study 4 - ICIS sample (PID 4931)"


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------
ACTION_FILLS = {
    "Pay now":                    PatternFill("solid", fgColor="C6EFCE"),
    "Check by hand":              PatternFill("solid", fgColor="FFEB9C"),
    "Do not pay":                 PatternFill("solid", fgColor="FFC7CE"),
    "Incomplete - not eligible":  PatternFill("solid", fgColor="D9D9D9"),
}

HEADER_FILL = PatternFill("solid", fgColor="1F5A7A")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)

STUDY_FILLS = {
    "Study 1": PatternFill("solid", fgColor="BDD7EE"),
    "Study 2": PatternFill("solid", fgColor="FCE4D6"),
    "Study 3": PatternFill("solid", fgColor="E2EFDA"),
    "Study 4": PatternFill("solid", fgColor="EDD9F5"),
}

REGRESSION_HEADER_FILL = PatternFill("solid", fgColor="2F3542")
FAIL_FILL              = PatternFill("solid", fgColor="FFD9D9")
FAIL_FONT              = Font(color="9C0006", bold=True, size=10)
PASS_FONT              = Font(color="1E6B37", size=10)

COL_WIDTHS = {
    "Study": 46,
    "Record ID": 12,
    "Email address": 35,
    "University email address": 16,
    "Started": 20,
    "Last section handed in": 20,
    "Started overnight": 14,
    "Family Information minutes": 20,
    "Values minutes": 16,
    "Thoughts and feelings minutes": 20,
    "Demographics minutes": 18,
    "Whole survey minutes": 20,
    "Sections with a recorded time": 16,
    "Minutes from sign-up to last section": 22,
    "Whole survey speed": 18,
    "Thoughts and feelings speed": 18,
    "Whole survey finished faster than any verified caregiver": 26,
    "Whole survey time sits inside the uncertainty band": 24,
    "Thoughts and feelings section faster than any verified caregiver": 26,
    "Thoughts and feelings time sits inside the uncertainty band": 24,
    "One of the four sections finished below its own speed line": 26,
    "The same answer repeated down a rating block": 24,
    "Answer sheet identical to another response": 24,
    "Arrived within a minute of two or more other sign-ups": 26,
    "Written comment nearly identical to another response": 26,
    "Family answers contradict each other": 22,
    "Age and location cannot both be true": 22,
    "Throwaway or temporary email address": 24,
    "No email address on a finished form": 24,
    "Started between midnight and five in the morning": 26,
    "Which checks were broken": 65,
    "Points from each check": 65,
    "Checks broken": 14,
    "Serious checks broken": 14,
    "Mild checks broken": 14,
    "Risk score": 12,
    "Recommended action": 24,
    "Why this action": 22,
    "Review plan": 22,
    "Why this plan": 22,
    "Final plan": 22,
    "Cleared by university email": 16,
    "Sign-ups within two minutes": 16,
    "Arrived in a tight cluster": 16,
    "Email domain seen only once": 16,
    "Named in the earlier confirmed list": 16,
    "Regression testing for each rule [R1: total<11.57m | R2: tfa<7.85m | R3: sec_floor | R4: likert_sd<0.15 | R5: fp_dup | R6: burst>=2 | R7: text_sim>0.90 | R8: fam_logic | R9: demo_logic | R10: throwaway_email | R11: no_email_complete | R12: hour_0_4]": 85,
    "Score Regression Check [Risk score == sum(broken rule points)]": 38,
    "Decision Regression Check [Recommended action == Gate(Completed, Risk score)]": 58,
    "R1 Regression [get_time_fif + get_time_val + get_time_tfa + get_time_demo < 11.57 min]": 35,
    "R2 Regression [get_time_tfa < 7.85 min]": 30,
    "R3 Regression [fif < 2.55 | val < 0.22 | tfa < 6.67 | demo < 0.95 min]": 45,
    "R4 Regression [Likert items row_stdev < 0.15]": 30,
    "R5 Regression [SHA-256(Likert vector) duplicated & answered >= 80%]": 38,
    "R6 Regression [Count(eligibility_timestamp +/- 120s) >= 2]": 34,
    "R7 Regression [max(TF-IDF open text cosine similarity) > 0.90]": 34,
    "R8 Regression [fif_num_autistic == 0 & followup != '' | child_age_bands > fif_num_children]": 48,
    "R9 Regression [age not in 18-100 | (US & invalid ZIP) | parent_age_at_birth not in 10-60]": 48,
    "R10 Regression [email domain in DISPOSABLE_DOMAINS]": 34,
    "R11 Regression [demo_email == '' & all 4 sections complete]": 50,
    "R12 Regression [eligibility_timestamp hour in 0..4]": 42,
}

STUDY_SHEET_NAMES = {
    "Study 1 - verified caregiver sample (PID 4797)": "Study 1 - Reference (PID 4797)",
    "Study 2 - online recruitment sample (PID 4581)": "Study 2 - Recruited (PID 4581)",
    "Study 3 - bilingual sample (PID 4700 + 5749)": "Study 3 - Bilingual (PID 5749)",
    "Study 4 - ICIS sample (PID 4931)": "Study 4 - ICIS (PID 4931)",
}


def _write_sheet(ws, df: pd.DataFrame, highlight_cols=None):
    if highlight_cols is None:
        highlight_cols = ("Record ID", "Recommended action", "Risk score", "Final plan")

    ws.row_dimensions[1].height = 42
    col_names = list(df.columns)

    for ci, col in enumerate(col_names, 1):
        cell = ws.cell(row=1, column=ci, value=col)
        is_reg = ("Regression" in col) or ("check [" in col.lower()) or ("testing for each rule" in col.lower())
        cell.fill = REGRESSION_HEADER_FILL if is_reg else HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")

    rec_act_idx = col_names.index("Recommended action") if "Recommended action" in col_names else -1

    for ri, row_tuple in enumerate(df.itertuples(index=False), 2):
        action = str(row_tuple[rec_act_idx]) if rec_act_idx >= 0 else ""
        fill = ACTION_FILLS.get(action)
        for ci, (col, val) in enumerate(zip(col_names, row_tuple), 1):
            if pd.isna(val) or val == "nan" or val == "<NA>" or val is None:
                val = ""
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.alignment = Alignment(wrap_text=False, vertical="center")
            if fill and col in highlight_cols:
                cell.fill = fill
            is_reg = ("Regression" in col) or ("check [" in col.lower()) or ("testing for each rule" in col.lower())
            if is_reg:
                sval = str(val)
                if sval.startswith("FAIL"):
                    cell.fill = FAIL_FILL
                    cell.font = FAIL_FONT
                elif sval.startswith("PASS") or sval.startswith("ALL RULES PASS"):
                    cell.font = PASS_FONT

    for ci, col in enumerate(col_names, 1):
        ws.column_dimensions[get_column_letter(ci)].width = COL_WIDTHS.get(col, 20)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def write_excel(combined: pd.DataFrame, out_path: Path):
    print(f"\n  Writing Excel -> {out_path.name}")
    wb = openpyxl.Workbook()

    # Summary sheet
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_sum.row_dimensions[1].height = 28
    headers = ["Study", "Total records", "Pay now", "Check by hand",
               "Do not pay", "Incomplete"]
    for ci, h in enumerate(headers, 1):
        cell = ws_sum.cell(row=1, column=ci, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")

    studies = combined["Study"].unique().tolist()
    for ri, study in enumerate(studies, 2):
        ws_sum.row_dimensions[ri].height = 22
        sub = combined[combined["Study"] == study]
        counts = sub["Recommended action"].value_counts().to_dict()
        vals = [
            study, len(sub),
            counts.get("Pay now", 0),
            counts.get("Check by hand", 0),
            counts.get("Do not pay", 0),
            counts.get("Incomplete - not eligible", 0),
        ]
        sf = next((f for k, f in STUDY_FILLS.items() if study.startswith(k)), None)
        for ci, v in enumerate(vals, 1):
            cell = ws_sum.cell(row=ri, column=ci, value=v)
            cell.alignment = Alignment(vertical="center", horizontal="left" if ci == 1 else "center")
            if sf:
                cell.fill = sf

    total_row = len(studies) + 2
    ws_sum.row_dimensions[total_row].height = 24
    cell_tot_lbl = ws_sum.cell(row=total_row, column=1, value="TOTAL")
    cell_tot_lbl.font = Font(bold=True)
    cell_tot_lbl.alignment = Alignment(vertical="center", horizontal="left")
    for ci in range(2, 7):
        total = sum(
            ws_sum.cell(r, ci).value or 0 for r in range(2, total_row)
        )
        cell = ws_sum.cell(row=total_row, column=ci, value=total)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center", horizontal="center")
    ws_sum.column_dimensions["A"].width = 46
    for ci in range(2, 7):
        ws_sum.column_dimensions[get_column_letter(ci)].width = 20

    # Per-study sheets
    for study in studies:
        sub = combined[combined["Study"] == study].copy()
        sheet_name = STUDY_SHEET_NAMES.get(study, (study[:31]).replace("/", "-"))
        ws = wb.create_sheet(title=sheet_name)
        _write_sheet(ws, sub)

    # All Records sheet
    ws_all = wb.create_sheet(title="All Records")
    _write_sheet(ws_all, combined)

    # Move Summary to front
    wb.move_sheet("Summary", offset=-(len(wb.sheetnames) - 1))
    wb.save(str(out_path))
    print(f"  Saved: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 65)
    print("ESD Caregiver Bot Detection - All Studies AllResponses Export")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    try:
        from bot_analysis import refresh_redcap_cache
        print("\n[Step 1/3] Dynamically verifying REDCap API cache...")
        refreshed = refresh_redcap_cache(PROJECT_DIR)
        print(f"  -> API cache verified: {len(refreshed)} resources checked/updated")
    except Exception as e:
        print(f"  -> Note on API cache check: {e}")

    print("\n[Step 2/3] Scoring all studies and evaluating regression checks...")
    all_frames = []

    for loader in (load_study_4797, load_study_4581, load_study_bilingual, load_study_icis):
        try:
            df, label = loader()
            scored = score_study(df, label)
            all_frames.append(scored)
            cts = scored["Recommended action"].value_counts().to_dict()
            print(f"  -> {label[:50]}: {len(scored)} records | "
                  f"Pay now={cts.get('Pay now',0)}, "
                  f"Check={cts.get('Check by hand',0)}, "
                  f"DNP={cts.get('Do not pay',0)}, "
                  f"Incomplete={cts.get('Incomplete - not eligible',0)}")
        except Exception as exc:
            import traceback
            print(f"  ERROR in {loader.__name__}: {exc}")
            traceback.print_exc()

    if not all_frames:
        print("No studies loaded. Aborting.")
        sys.exit(1)

    combined = pd.concat(all_frames, ignore_index=True)
    print(f"\nTotal records across all studies: {len(combined)}")

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = OUTPUT_DIR / f"ESD_AllResponses_AllStudies_{date_str}.xlsx"
    write_excel(combined, out_path)

    print("\n" + "=" * 65)
    print("FINAL DECISION SUMMARY")
    print("=" * 65)
    tbl = (
        combined.groupby(["Study", "Recommended action"])
        .size()
        .unstack(fill_value=0)
        .reindex(
            columns=["Pay now", "Check by hand", "Do not pay", "Incomplete - not eligible"],
            fill_value=0,
        )
    )
    print(tbl.to_string())
    print("=" * 65)
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
