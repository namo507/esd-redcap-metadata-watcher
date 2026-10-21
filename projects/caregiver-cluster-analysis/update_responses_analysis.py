"""Rerun caregiver bot screening pipeline and update Responses Analysis_Revised (3).xlsx

Pulls live data from all REDCap APIs (PIDs 4797, 4581, 5749, 4931, 4700, 6277).
Updates all 8 sheets in 'Responses Analysis_Revised (3).xlsx':
1. Logic Rules: preserved rule catalog
2. Executive Summary: updated source, analyzed, approved, manual, dnp, incomplete metrics
3. Simplified All Responses: all 2,511 records with Verification Check 1 & 2
4. Pay Now (Approved): 407 validated records
5. Manual Review Queue: 505 ambiguous records
6. Do Not Pay (Flagged Bots): 503 bot/fraud records
7. Excluded Records: 819 protocol exclusions
8. Decision & Rule Guide: decision impact and updated verification snapshot totals
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

load_dotenv(PROJECT_DIR.parent.parent / ".env")

import refined_sources
import build_simplified_responses as bsr
import valid_data_clusters as vdc

ORIGINAL_NAMES = {
    4797: "Infant Autism Screening",
    4581: "old-Infant Autism Screening",
    5749: "Infant Autism Screening-27Sep25_bilingual-13Jul26",
    4931: "Infant Autism Screening - ICIS",
}

EDUCATION_MAP = bsr.EDUCATION_MAP
STUDY_NAMES = bsr.STUDY_NAMES
POPULATION_MAP = bsr.POPULATION_MAP


def main():
    print("=== 1. Loading REDCap Sources ===")
    sources_bundle = refined_sources.load_refined_sources(PROJECT_DIR, refresh=True)
    sim_df = bsr.build_simplified_records(sources_bundle)
    ex_df = sources_bundle.exclusions.copy()

    # Form Project Key
    sim_df["Project Key"] = sim_df["PID"].astype(str) + "-" + sim_df["Record_ID"].astype(str)
    ex_df["Project Key"] = ex_df["project_id"].astype(str) + "-" + ex_df["record_id"].astype(str)

    print(f"Analyzed records: {len(sim_df)}, Excluded records: {len(ex_df)}")

    print("=== 2. Pulling PID 6277 Verification Survey Data ===")
    url = os.environ.get("REDCAP_API_URL", "https://redcap.research.sc.edu/api/")
    token_6277 = os.environ.get("TOKEN_VERIFICATION_6277")
    if not token_6277:
        raise ValueError("TOKEN_VERIFICATION_6277 is required in .env.")
    r_6277 = requests.post(
        url,
        data={"token": token_6277, "content": "record", "format": "json", "returnFormat": "json"},
        timeout=60,
    )
    r_6277.raise_for_status()
    payload_6277 = r_6277.json()
    if not isinstance(payload_6277, list):
        raise ValueError("Unexpected PID 6277 payload: expected a JSON array of records.")
    df_6277 = pd.DataFrame(payload_6277)
    required_6277_cols = {"source_pid", "source_record_id"}
    missing_6277_cols = required_6277_cols - set(df_6277.columns)
    if missing_6277_cols:
        raise ValueError(f"PID 6277 payload missing required columns: {sorted(missing_6277_cols)}")

    df_6277["derived_key"] = df_6277["source_pid"].astype(str).str.strip() + "-" + df_6277["source_record_id"].astype(str).str.strip()
    df_6277 = df_6277[df_6277["derived_key"] != "-"].copy()

    if df_6277["derived_key"].duplicated().any():
        duplicate_keys = sorted(df_6277.loc[df_6277["derived_key"].duplicated(), "derived_key"].unique().tolist())
        raise ValueError(f"PID 6277 has duplicate source_pid/source_record_id mappings: {duplicate_keys[:10]}")

    # Key mapping lookup dict for 6277 records
    verif_lookup = {}
    for _, row in df_6277.iterrows():
        key = row["derived_key"]
        if key:
            verif_lookup[key] = row

    print(f"PID 6277 mapped records: {len(verif_lookup)}")

    print("=== 3. Computing Final Validity Determination ===")
    screen_for_validity = sim_df[["PID", "Record_ID", "Payment_Status"]].copy()
    screen_for_validity["response_key"] = (
        screen_for_validity["PID"].astype(str) + "-" + screen_for_validity["Record_ID"].astype(str)
    )
    all_responses_with_validity = vdc.assign_validity(
        vdc.fold_all_responses(sources_bundle),
        screen_for_validity,
        df_6277,
    )
    validity_lookup = all_responses_with_validity.set_index("response_key")[["valid", "verified", "route"]]
    expected_keys = set(sim_df["Project Key"]).union(set(ex_df["Project Key"]))
    missing_validity_keys = expected_keys - set(validity_lookup.index)
    if missing_validity_keys:
        sample_missing = sorted(list(missing_validity_keys))[:10]
        raise ValueError(
            f"{len(missing_validity_keys)} response keys are missing final validity mapping. Sample: {sample_missing}"
        )
    final_valid_count = int(validity_lookup["valid"].sum())
    officially_verified_count = int(validity_lookup["verified"].sum())
    print(
        f"Final valid responses: {final_valid_count} of {len(validity_lookup)}; "
        f"officially verified in PID 6277: {officially_verified_count}"
    )

    print("=== 4. Building Executive Summary ===")
    summary_rows = []
    for pid in [4797, 4581, 5749, 4931]:
        sub_sim = sim_df[sim_df["PID"] == pid]
        sub_ex = ex_df[ex_df["project_id"].astype(int) == pid]

        total_raw = len(sub_sim) + len(sub_ex)
        ex_count = len(sub_ex)
        analyzed_count = len(sub_sim)

        pay_now = int((sub_sim["Payment_Status"] == "Pay Now").sum())
        manual = int((sub_sim["Payment_Status"] == "Manual Review").sum())
        dnp = int((sub_sim["Payment_Status"] == "Do Not Pay").sum())
        incomplete = int((sub_sim["Payment_Status"] == "Incomplete - Not Eligible").sum())

        approval_rate = f"{(pay_now / analyzed_count * 100):.1f}%" if analyzed_count > 0 else "0.0%"

        summary_rows.append({
            "Original Name": ORIGINAL_NAMES.get(pid, ""),
            "Study Name": STUDY_NAMES.get(pid, f"Study {pid}"),
            "REDCap PID": pid,
            "Recruitment Population": POPULATION_MAP.get(pid, ""),
            "Total Source Records": total_raw,
            "Hard Excluded Records": ex_count,
            "Analyzed Records": analyzed_count,
            "Pay Now (Approved)": pay_now,
            "Manual Review": manual,
            "Do Not Pay (Flagged Bots)": dnp,
            "Incomplete - Not Eligible": incomplete,
            "Approval Rate (% Analyzed)": approval_rate,
        })

    summary_df = pd.DataFrame(summary_rows)

    # Totals row
    tot_raw = summary_df["Total Source Records"].sum()
    tot_ex = summary_df["Hard Excluded Records"].sum()
    tot_analyzed = summary_df["Analyzed Records"].sum()
    tot_pay = summary_df["Pay Now (Approved)"].sum()
    tot_manual = summary_df["Manual Review"].sum()
    tot_dnp = summary_df["Do Not Pay (Flagged Bots)"].sum()
    tot_inc = summary_df["Incomplete - Not Eligible"].sum()
    tot_rate = f"{(tot_pay / tot_analyzed * 100):.1f}%" if tot_analyzed > 0 else "0.0%"

    totals_row = pd.DataFrame([{
        "Original Name": "Source: REDCap",
        "Study Name": "All Studies (Combined Total)",
        "REDCap PID": "All",
        "Recruitment Population": "Full Multi-Study Cohort",
        "Total Source Records": tot_raw,
        "Hard Excluded Records": tot_ex,
        "Analyzed Records": tot_analyzed,
        "Pay Now (Approved)": tot_pay,
        "Manual Review": tot_manual,
        "Do Not Pay (Flagged Bots)": tot_dnp,
        "Incomplete - Not Eligible": tot_inc,
        "Approval Rate (% Analyzed)": tot_rate,
    }])
    exec_summary_df = pd.concat([summary_df, totals_row], ignore_index=True)

    print("=== 5. Building Verification Check Columns for Simplified All Responses ===")
    all_responses_rows = []

    # Rename map for analyzed records to match exact format
    rename_cols = {
        "Study_Name": "Study Name",
        "PID": "REDCap PID",
        "Record_ID": "Record ID",
        "Payment_Status": "Payment Status",
        "Rule_Violations": "Rule Violations",
        "Payment_Recommendation_Notes": "Payment Recommendation Notes",
        "Total_Violations_Count": "Total Violations Count",
        "Survey_Completed": "Survey Completed",
        "Total_Survey_Time_Min": "Total Survey Time (min)",
        "TFA_Section_Time_Min": "Thoughts & Feelings Time (min)",
        "Email_Address": "Email Address",
        "Email_Usable": "Email Usable",
        "Attention_Check_Response": "Attention Check Response",
        "Caregiver_Pronouns": "Caregiver Pronouns",
        "Caregiver_Gender": "Caregiver Gender",
        "Number_of_Children": "Number of Children",
        "Autistic_Children_Count": "Autistic Children Count",
        "Children_Special_Needs": "Children Special Needs/Disability",
        "Highest_Education_Level": "Highest Education Level",
        "Employment_Status": "Employment Status",
        "Home_ZIP_Code": "Home ZIP Code",
        "Living_Area": "Living Area Description",
        "Caregiver_Age": "Caregiver Age",
        "Started_Timestamp": "Date/Time Started",
        "Completed_Timestamp": "Date/Time Completed",
        "Contains_Test_Word": "Contains 'test' in Text",
    }
    sim_df_renamed = sim_df.rename(columns=rename_cols)

    # 4A. Process Analyzed Records
    for _, row in sim_df_renamed.iterrows():
        key = row["Project Key"]
        v_rec = verif_lookup.get(key)
        validity_row = validity_lookup.loc[key]
        final_verification = "Valid" if bool(validity_row["valid"]) else "Not Valid"

        # Verification Check 1: Pay Now -> PASS; Do Not Pay -> FAIL; others keep category
        p_stat = row["Payment Status"]
        if p_stat == "Pay Now":
            check1 = "PASS"
        elif p_stat == "Do Not Pay":
            check1 = "FAIL"
        else:
            check1 = p_stat

        # Verification Check 2 component codes
        if v_rec is None or str(v_rec.get("eligible_for_verification", "")).strip() != "1":
            check2_zip = "NA"
            check2_edu = "NA"
            check2 = "NA"
        elif str(v_rec.get("caregiver_verification_complete", "")).strip() != "2":
            check2_zip = "NR"
            check2_edu = "NR"
            check2 = "NR"
        else:
            # Completed verification survey
            orig_zip = str(v_rec.get("original_zip", "")).strip()
            ver_zip = str(v_rec.get("verify_zip", "")).strip()
            mis_zip = str(v_rec.get("mismatch_zip", "")).strip()

            if not orig_zip:
                check2_zip = "MO"
            elif not ver_zip:
                check2_zip = "NR"
            elif mis_zip == "0":
                check2_zip = 1
            elif mis_zip == "1":
                check2_zip = 0
            else:
                check2_zip = 1 if orig_zip == ver_zip else 0

            orig_edu = str(v_rec.get("original_education", "")).strip()
            ver_edu = str(v_rec.get("verify_education", "")).strip()
            mis_edu = str(v_rec.get("mismatch_education", "")).strip()

            if not orig_edu:
                check2_edu = "MO"
            elif not ver_edu:
                check2_edu = "NR"
            elif mis_edu == "0":
                check2_edu = 1
            elif mis_edu == "1":
                check2_edu = 0
            else:
                check2_edu = 1 if orig_edu == ver_edu else 0

            check2 = "PASS" if (check2_zip == 1 and check2_edu == 1) else "FAIL"

        all_responses_rows.append({
            "Study Name": row["Study Name"],
            "Project Key": key,
            "REDCap PID": row["REDCap PID"],
            "Record ID": row["Record ID"],
            "Verification Check 1": check1,
            "Verification Check 2 - Caregiver Email": check2,
            "VerificationCheck2_zipcode": check2_zip,
            "VerificationCheck2_edu": check2_edu,
            "Final Verification": final_verification,
            "Rule Violations": row["Rule Violations"],
            "Payment Recommendation Notes": row["Payment Recommendation Notes"],
            "Total Violations Count": row["Total Violations Count"],
            "Survey Completed": row["Survey Completed"],
            "Total Survey Time (min)": row["Total Survey Time (min)"],
            "Thoughts & Feelings Time (min)": row["Thoughts & Feelings Time (min)"],
            "Email Address": row["Email Address"],
            "Email Usable": row["Email Usable"],
            "Attention Check Response": row["Attention Check Response"],
            "Caregiver Pronouns": row["Caregiver Pronouns"],
            "Caregiver Gender": row["Caregiver Gender"],
            "Number of Children": row["Number of Children"],
            "Autistic Children Count": row["Autistic Children Count"],
            "Children Special Needs/Disability": row["Children Special Needs/Disability"],
            "Highest Education Level": row["Highest Education Level"],
            "Employment Status": row["Employment Status"],
            "Home ZIP Code": row["Home ZIP Code"],
            "Living Area Description": row["Living Area Description"],
            "Caregiver Age": row["Caregiver Age"],
            "Date/Time Started": row["Date/Time Started"],
            "Date/Time Completed": row["Date/Time Completed"],
            "Contains 'test' in Text": row["Contains 'test' in Text"],
        })

    # 4B. Process Excluded Records
    for _, row in ex_df.iterrows():
        key = row["Project Key"]
        v_rec = verif_lookup.get(key)
        validity_row = validity_lookup.loc[key]
        final_verification = "Valid" if bool(validity_row["valid"]) else "Not Valid"
        pid = int(row["project_id"])
        rid = str(row["record_id"])
        study_name = STUDY_NAMES.get(pid, f"Study {pid}")
        ex_reason = str(row["exclusion_reason"])
        matched_txt = str(row.get("matched_text_fields", ""))

        check1 = "Excluded - Do Not Pay"

        if v_rec is None or str(v_rec.get("eligible_for_verification", "")).strip() != "1":
            check2_zip = None
            check2_edu = None
            check2 = None
            orig_zip_val = None
            orig_edu_val = None
        elif str(v_rec.get("caregiver_verification_complete", "")).strip() != "2":
            check2_zip = "NR"
            check2_edu = "NR"
            check2 = "NR"
            orig_zip_val = str(v_rec.get("original_zip", "")).strip() or None
            edu_raw = str(v_rec.get("original_education", "")).strip()
            orig_edu_val = EDUCATION_MAP.get(edu_raw, edu_raw) if edu_raw else None
        else:
            orig_zip = str(v_rec.get("original_zip", "")).strip()
            ver_zip = str(v_rec.get("verify_zip", "")).strip()
            mis_zip = str(v_rec.get("mismatch_zip", "")).strip()

            if not orig_zip:
                check2_zip = "MO"
            elif not ver_zip:
                check2_zip = "NR"
            elif mis_zip == "0":
                check2_zip = 1
            elif mis_zip == "1":
                check2_zip = 0
            else:
                check2_zip = 1 if orig_zip == ver_zip else 0

            orig_edu = str(v_rec.get("original_education", "")).strip()
            ver_edu = str(v_rec.get("verify_education", "")).strip()
            mis_edu = str(v_rec.get("mismatch_education", "")).strip()

            if not orig_edu:
                check2_edu = "MO"
            elif not ver_edu:
                check2_edu = "NR"
            elif mis_edu == "0":
                check2_edu = 1
            elif mis_edu == "1":
                check2_edu = 0
            else:
                check2_edu = 1 if orig_edu == ver_edu else 0

            check2 = "PASS" if (check2_zip == 1 and check2_edu == 1) else "FAIL"
            orig_zip_val = orig_zip or None
            orig_edu_val = EDUCATION_MAP.get(orig_edu, orig_edu) if orig_edu else None

        note_str = f"Excluded from original screening: {ex_reason}"
        if matched_txt and matched_txt != "nan":
            note_str += f". Matched fields: {matched_txt}"

        all_responses_rows.append({
            "Study Name": study_name,
            "Project Key": key,
            "REDCap PID": pid,
            "Record ID": rid,
            "Verification Check 1": check1,
            "Verification Check 2 - Caregiver Email": check2,
            "VerificationCheck2_zipcode": check2_zip,
            "VerificationCheck2_edu": check2_edu,
            "Final Verification": final_verification,
            "Rule Violations": ex_reason,
            "Payment Recommendation Notes": note_str,
            "Total Violations Count": None,
            "Survey Completed": None,
            "Total Survey Time (min)": None,
            "Thoughts & Feelings Time (min)": None,
            "Email Address": None,
            "Email Usable": None,
            "Attention Check Response": None,
            "Caregiver Pronouns": None,
            "Caregiver Gender": None,
            "Number of Children": None,
            "Autistic Children Count": None,
            "Children Special Needs/Disability": None,
            "Highest Education Level": orig_edu_val,
            "Employment Status": None,
            "Home ZIP Code": orig_zip_val,
            "Living Area Description": None,
            "Caregiver Age": None,
            "Date/Time Started": None,
            "Date/Time Completed": None,
            "Contains 'test' in Text": "Yes" if bool(row.get("contains_test_word")) else "No",
        })

    all_responses_df = pd.DataFrame(all_responses_rows)
    print(f"Total rows in Simplified All Responses: {len(all_responses_df)}")

    # 5. Filtered Sheets
    pay_now_df = sim_df_renamed[sim_df_renamed["Payment Status"] == "Pay Now"].drop(columns=["Project Key"]).copy()
    manual_review_df = sim_df_renamed[sim_df_renamed["Payment Status"] == "Manual Review"].drop(columns=["Project Key"]).copy()
    do_not_pay_df = sim_df_renamed[sim_df_renamed["Payment Status"] == "Do Not Pay"].drop(columns=["Project Key"]).copy()

    # Excluded Records sheet
    ex_clean_rows = []
    for _, row in ex_df.iterrows():
        pid = int(row["project_id"])
        ex_clean_rows.append({
            "Study Name": STUDY_NAMES.get(pid, f"Study {pid}"),
            "PID": pid,
            "Record ID": row["record_id"],
            "Payment Status": "Excluded - Do Not Pay",
            "Exclusion Reason": row["exclusion_reason"],
            "Matched Text Fields": row.get("matched_text_fields", ""),
        })
    ex_clean_df = pd.DataFrame(ex_clean_rows)

    # 7. Logic Rules & Decision Guide
    orig_wb = openpyxl.load_workbook(PROJECT_DIR / "archive/Responses Analysis_Revised (3)_pre_oct21_backup.xlsx", data_only=True)
    logic_rules_df = pd.read_excel(PROJECT_DIR / "archive/Responses Analysis_Revised (3)_pre_oct21_backup.xlsx", sheet_name="Logic Rules")
    rule_guide_df = pd.read_excel(PROJECT_DIR / "archive/Responses Analysis_Revised (3)_pre_oct21_backup.xlsx", sheet_name="Decision & Rule Guide")

    # Update Rule 19 in rule_guide_df with latest counts
    p_cnt = int((all_responses_df["Verification Check 2 - Caregiver Email"] == "PASS").sum())
    f_cnt = int((all_responses_df["Verification Check 2 - Caregiver Email"] == "FAIL").sum())
    nr_cnt = int((all_responses_df["Verification Check 2 - Caregiver Email"] == "NR").sum())
    na_cnt = len(all_responses_df) - (p_cnt + f_cnt + nr_cnt)

    for idx, r_row in rule_guide_df.iterrows():
        if r_row["Rule Name"] == "Verification snapshot totals":
            rule_guide_df.at[idx, "Logic & Threshold"] = (
                f"{p_cnt} PASS; {f_cnt} FAIL; {nr_cnt} NR; {na_cnt} NA across {len(all_responses_df):,} records. "
                f"Officially verified by PID 6277 (all three answers match): {officially_verified_count}. "
                f"Final valid responses (Pay Now or verified): {final_valid_count}. "
                "Snapshot refreshed October 21, 2026 from live REDCap APIs."
            )
            rule_guide_df.at[idx, "Weight / Severity"] = "2026-10-21"

    print("=== 6. Writing to Responses Analysis_Revised (3).xlsx ===")
    out_file = PROJECT_DIR / "Responses Analysis_Revised (3).xlsx"

    with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
        logic_rules_df.to_excel(writer, sheet_name="Logic Rules", index=False)
        exec_summary_df.to_excel(writer, sheet_name="Executive Summary", index=False)
        all_responses_df.to_excel(writer, sheet_name="Simplified All Responses", index=False)
        pay_now_df.to_excel(writer, sheet_name="Pay Now (Approved)", index=False)
        manual_review_df.to_excel(writer, sheet_name="Manual Review Queue", index=False)
        do_not_pay_df.to_excel(writer, sheet_name="Do Not Pay (Flagged Bots)", index=False)
        ex_clean_df.to_excel(writer, sheet_name="Excluded Records", index=False)
        rule_guide_df.to_excel(writer, sheet_name="Decision & Rule Guide", index=False)

    print("=== 7. Applying Styling and Formatting ===")
    wb = openpyxl.load_workbook(out_file)

    navy_header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    pay_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    pay_font = Font(name="Calibri", size=11, color="375623", bold=True)

    review_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    review_font = Font(name="Calibri", size=11, color="7F6000", bold=True)

    dnp_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    dnp_font = Font(name="Calibri", size=11, color="C65911", bold=True)

    inc_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    inc_font = Font(name="Calibri", size=11, color="595959")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    for sheetname in wb.sheetnames:
        ws = wb[sheetname]
        ws.views.sheetView[0].showGridLines = True

        # Header formatting
        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = navy_header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)

        # Freeze panes & specific styles
        if sheetname == "Simplified All Responses":
            ws.freeze_panes = "F2"
            header_idx = {
                str(ws.cell(row=1, column=col_idx).value or ""): col_idx
                for col_idx in range(1, ws.max_column + 1)
            }
            center_headers = [
                "Project Key",
                "REDCap PID",
                "Record ID",
                "Verification Check 1",
                "Verification Check 2 - Caregiver Email",
                "VerificationCheck2_zipcode",
                "VerificationCheck2_edu",
                "Final Verification",
                "Total Violations Count",
                "Survey Completed",
                "Email Usable",
                "Home ZIP Code",
                "Contains 'test' in Text",
            ]
            right_headers = [
                "Total Survey Time (min)",
                "Thoughts & Feelings Time (min)",
            ]
            center_cols = {header_idx[h] for h in center_headers if h in header_idx}
            right_cols = {header_idx[h] for h in right_headers if h in header_idx}
            v1_col = header_idx.get("Verification Check 1")
            v2_col = header_idx.get("Verification Check 2 - Caregiver Email")
            final_col = header_idx.get("Final Verification")

            for row_idx in range(2, ws.max_row + 1):
                status_cell = ws.cell(row=row_idx, column=v1_col) if v1_col else None
                if status_cell is None:
                    continue
                status_val = str(status_cell.value or "").strip()
                if status_val == "PASS":
                    status_cell.fill = pay_fill
                    status_cell.font = pay_font
                elif status_val == "Manual Review":
                    status_cell.fill = review_fill
                    status_cell.font = review_font
                elif status_val == "FAIL":
                    status_cell.fill = dnp_fill
                    status_cell.font = dnp_font
                elif "Incomplete" in status_val or "Excluded" in status_val:
                    status_cell.fill = inc_fill
                    status_cell.font = inc_font

                if v2_col:
                    v2_cell = ws.cell(row=row_idx, column=v2_col)
                    v2_val = str(v2_cell.value or "").strip()
                    if v2_val == "PASS":
                        v2_cell.fill = pay_fill
                        v2_cell.font = pay_font
                    elif v2_val == "FAIL":
                        v2_cell.fill = dnp_fill
                        v2_cell.font = dnp_font
                    elif v2_val == "NR":
                        v2_cell.fill = review_fill
                        v2_cell.font = review_font

                if final_col:
                    final_cell = ws.cell(row=row_idx, column=final_col)
                    final_val = str(final_cell.value or "").strip()
                    if final_val == "Valid":
                        final_cell.fill = pay_fill
                        final_cell.font = pay_font
                    elif final_val == "Not Valid":
                        final_cell.fill = dnp_fill
                        final_cell.font = dnp_font

                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.border = thin_border
                    if c_idx in center_cols:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                    elif c_idx in right_cols:
                        c.alignment = Alignment(horizontal="right", vertical="center")
                        c.number_format = "0.00"

        elif sheetname in ["Pay Now (Approved)", "Manual Review Queue", "Do Not Pay (Flagged Bots)"]:
            ws.freeze_panes = "E2"
            for row_idx in range(2, ws.max_row + 1):
                status_cell = ws.cell(row=row_idx, column=4)
                status_val = str(status_cell.value or "").strip()
                if status_val == "Pay Now":
                    status_cell.fill = pay_fill
                    status_cell.font = pay_font
                elif status_val == "Manual Review":
                    status_cell.fill = review_fill
                    status_cell.font = review_font
                elif status_val == "Do Not Pay":
                    status_cell.fill = dnp_fill
                    status_cell.font = dnp_font

                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.border = thin_border
                    if c_idx in [2, 3, 7, 8, 12, 22]:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                    elif c_idx in [9, 10]:
                        c.alignment = Alignment(horizontal="right", vertical="center")
                        c.number_format = "0.00"

        elif sheetname == "Executive Summary":
            ws.freeze_panes = "A2"
            for row_idx in range(2, ws.max_row + 1):
                is_total = (row_idx == ws.max_row)
                row_font = Font(name="Calibri", size=11, bold=is_total)
                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.font = row_font
                    c.border = thin_border
                    if c_idx >= 5:
                        c.alignment = Alignment(horizontal="right", vertical="center")
                    if is_total:
                        c.fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")

        elif sheetname == "Excluded Records":
            ws.freeze_panes = "D2"
            for row_idx in range(2, ws.max_row + 1):
                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.border = thin_border
                    if c_idx in [2, 3]:
                        c.alignment = Alignment(horizontal="center", vertical="center")

        elif sheetname in ["Logic Rules", "Decision & Rule Guide"]:
            ws.freeze_panes = "A2"
            for row_idx in range(2, ws.max_row + 1):
                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.border = thin_border
                    c.alignment = Alignment(vertical="top", wrap_text=True)

        # Auto-adjust widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col[:100]:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            adjusted_width = min(65, max(12, max_len + 3))
            ws.column_dimensions[col_letter].width = adjusted_width

    wb.save(out_file)
    print(f"Successfully updated '{out_file}' ({out_file.stat().st_size:,} bytes).")

    # Also sync Simplified all responses.xlsx
    print("=== 8. Updating Simplified all responses.xlsx ===")
    bsr.generate_simplified_all_responses_excel(project_dir=PROJECT_DIR, refresh=True)


if __name__ == "__main__":
    main()
