#!/usr/bin/env python3
"""
Score and verify responses from PID 6277 (Caregiver - Respondent Verification Survey)
against ground-truth participant records in PID 4700 (Infant Autism Screening archive).

Outputs an automated verification audit scorecard with mismatch counts and verdict.
"""

import os
import sys
import json
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_URL = os.environ.get("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")
TOKEN_6277 = os.environ.get("TOKEN_CAREGIVER_6277", os.environ.get("TOKEN_6277"))
TOKEN_4700 = os.environ.get("TOKEN_ARCHIVE_4700", os.environ.get("TOKEN_4700"))

def fetch_records(token, fields=None):
    data = {
        "token": token,
        "content": "record",
        "format": "json",
        "returnFormat": "json"
    }
    if fields:
        data["fields"] = ",".join(fields)
    r = requests.post(API_URL, data=data, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"REDCap API error {r.status_code}: {r.text}")
    return r.json()

def run_verification_audit(output_csv="projects/caregiver-cluster-analysis/Caregiver Outputs/verification_survey_results.csv"):
    if not TOKEN_6277 or not TOKEN_4700:
        print("Error: Missing TOKEN_CAREGIVER_6277 or TOKEN_ARCHIVE_4700 in .env")
        sys.exit(1)

    print("Fetching survey responses from PID 6277...")
    recs_6277 = fetch_records(TOKEN_6277)
    print(f"Total records in PID 6277: {len(recs_6277)}")

    # Filter to completed responses (where verify_birth_year or verify_knee is filled)
    completed_6277 = [r for r in recs_6277 if str(r.get("verify_birth_year", "")).strip() != ""]
    print(f"Completed responses in PID 6277 so far: {len(completed_6277)}")

    if not completed_6277:
        print("No completed verification survey responses found yet in PID 6277.")
        print("Once respondents submit the survey on Monday, re-run this script to generate scores.")
        return

    record_ids = [r["record_id"] for r in completed_6277]
    print(f"Fetching ground-truth records from PID 4700 for {len(record_ids)} respondents...")
    
    fields_4700 = [
        "record_id", "demo_email", "demo_momdob", "demo_gender", "pronouns_elig",
        "zip_demo", "demo_area", "fif_num_children", "dob_child1", "fif_child_needs",
        "fif_num_autistic", "demo_cg1education", "demo_cg1employment", "elig_knee"
    ]
    recs_4700 = fetch_records(TOKEN_4700, fields=fields_4700)
    map_4700 = {str(r["record_id"]): r for r in recs_4700}

    results = []
    for resp in completed_6277:
        rid = str(resp.get("record_id"))
        gt = map_4700.get(rid, {})

        mismatches = 0
        checks_performed = 0
        details = []

        # 1. Email check
        resp_email = str(resp.get("participant_email", "")).strip().lower()
        gt_email = str(gt.get("demo_email", "")).strip().lower()
        if gt_email:
            checks_performed += 1
            if resp_email != gt_email:
                mismatches += 1
                details.append(f"Email mismatch (Survey: {resp_email} vs GT: {gt_email})")

        # 2. Birth Year check
        resp_by = str(resp.get("verify_birth_year", "")).strip()
        gt_dob = str(gt.get("demo_momdob", "")).strip()
        gt_by = gt_dob[:4] if len(gt_dob) >= 4 else ""
        if gt_by:
            checks_performed += 1
            if resp_by != gt_by:
                mismatches += 1
                details.append(f"Birth year mismatch (Survey: {resp_by} vs GT: {gt_by})")

        # 3. Gender check
        resp_gender = str(resp.get("verify_gender", "")).strip()
        gt_gender = str(gt.get("demo_gender", "")).strip()
        if gt_gender:
            checks_performed += 1
            if resp_gender != gt_gender:
                mismatches += 1
                details.append(f"Gender mismatch (Survey: {resp_gender} vs GT: {gt_gender})")

        # 4. Pronouns check
        resp_pronouns = str(resp.get("verify_pronouns", "")).strip()
        gt_pronouns = str(gt.get("pronouns_elig", "")).strip()
        if gt_pronouns:
            checks_performed += 1
            if resp_pronouns != gt_pronouns:
                mismatches += 1
                details.append(f"Pronouns mismatch (Survey: {resp_pronouns} vs GT: {gt_pronouns})")

        # 5. ZIP check
        resp_zip = str(resp.get("verify_zip", "")).strip()
        gt_zip = str(gt.get("zip_demo", "")).strip()
        if gt_zip:
            checks_performed += 1
            if resp_zip != gt_zip:
                mismatches += 1
                details.append(f"ZIP mismatch (Survey: {resp_zip} vs GT: {gt_zip})")

        # 6. Living Area check
        resp_area = str(resp.get("verify_area", "")).strip()
        gt_area = str(gt.get("demo_area", "")).strip()
        if gt_area:
            checks_performed += 1
            if resp_area != gt_area:
                mismatches += 1
                details.append(f"Area mismatch (Survey: {resp_area} vs GT: {gt_area})")

        # 7. Number of Children check
        resp_kids = str(resp.get("verify_num_children", "")).strip()
        gt_kids = str(gt.get("fif_num_children", "")).strip()
        if gt_kids:
            checks_performed += 1
            if resp_kids != gt_kids:
                mismatches += 1
                details.append(f"Num children mismatch (Survey: {resp_kids} vs GT: {gt_kids})")

        # 8. First child birth year (optional)
        resp_child_by = str(resp.get("verify_child_birth_year", "")).strip()
        gt_child_by = str(gt.get("dob_child1", "")).strip()
        if gt_child_by and resp_child_by:
            checks_performed += 1
            if resp_child_by != gt_child_by:
                mismatches += 1
                details.append(f"First child birth year mismatch (Survey: {resp_child_by} vs GT: {gt_child_by})")

        # 9. Special needs check
        resp_needs = str(resp.get("verify_child_needs", "")).strip()
        gt_needs = str(gt.get("fif_child_needs", "")).strip()
        if gt_needs:
            checks_performed += 1
            if resp_needs != gt_needs:
                mismatches += 1
                details.append(f"Special needs mismatch (Survey: {resp_needs} vs GT: {gt_needs})")

        # 10. Number of Autistic Children check
        resp_aut = str(resp.get("verify_num_autistic", "")).strip()
        gt_aut = str(gt.get("fif_num_autistic", "")).strip()
        if gt_aut:
            checks_performed += 1
            if resp_aut != gt_aut:
                mismatches += 1
                details.append(f"Autistic children count mismatch (Survey: {resp_aut} vs GT: {gt_aut})")

        # 11. Education check
        resp_edu = str(resp.get("verify_education", "")).strip()
        gt_edu = str(gt.get("demo_cg1education", "")).strip()
        if gt_edu:
            checks_performed += 1
            if resp_edu != gt_edu:
                mismatches += 1
                details.append(f"Education mismatch (Survey: {resp_edu} vs GT: {gt_edu})")

        # 12. Employment check
        resp_emp = str(resp.get("verify_employment", "")).strip()
        gt_emp = str(gt.get("demo_cg1employment", "")).strip()
        if gt_emp:
            checks_performed += 1
            if resp_emp != gt_emp:
                mismatches += 1
                details.append(f"Employment mismatch (Survey: {resp_emp} vs GT: {gt_emp})")

        # 13. Attention check (verify_knee == '2')
        resp_knee = str(resp.get("verify_knee", "")).strip()
        passed_attention = (resp_knee == "2")
        if not passed_attention:
            mismatches += 1
            details.append(f"Failed attention check (Answered: {resp_knee}, Expected: 2 [Band-Aid])")

        # Score & verdict
        match_rate = ((checks_performed - mismatches) / checks_performed) * 100.0 if checks_performed > 0 else 0.0

        if passed_attention and mismatches <= 1:
            verdict = "VERIFIED_HUMAN"
            recommendation = "APPROVE_GIFT_CARD"
        elif passed_attention and mismatches <= 3:
            verdict = "SUSPECT_MANUAL_REVIEW"
            recommendation = "HOLD_FOR_INSPECTION"
        else:
            verdict = "CONFIRMED_FARM_BOT"
            recommendation = "REJECT_NO_PAYMENT"

        results.append({
            "record_id": rid,
            "email": resp_email,
            "checks_performed": checks_performed,
            "mismatches": mismatches,
            "match_rate_pct": round(match_rate, 1),
            "passed_attention_check": passed_attention,
            "verdict": verdict,
            "recommendation": recommendation,
            "mismatch_details": "; ".join(details)
        })

    df = pd.DataFrame(results)
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"\nVerification audit saved to: {output_csv}")
    print(df["verdict"].value_counts())

if __name__ == "__main__":
    run_verification_audit()
