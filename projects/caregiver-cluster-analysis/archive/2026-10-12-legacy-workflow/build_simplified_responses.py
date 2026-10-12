"""Build stakeholder-friendly 'Simplified all responses.xlsx' workbook.

Implements the ESD Lab caregiver bot detection and payment review logic:
1. Dynamic Study Metadata: Map each PID to human-readable Study_Name, leading columns: Study_Name, PID, Record_ID.
2. Hard Exclusions: PID 5749 records 1-445 (archived clones from 4700), pre-launch 446-473, and 'test' in free-text.
3. Bot & Contradiction Detection:
   - Attention Check: Flag any answer other than 'Puts on a Band-Aid' for elig_knee.
   - Demographic Contradiction: Flag mismatched profiles (e.g. Pronouns='She' with Gender='Man').
   - Clinical Contradiction: Flag 'No disability' with >=1 autistic child.
   - Combine all active flags into a pipe-delimited Rule_Violations column.
4. Decision Engine (Payment_Status):
   - 'Pay Now': 0 violations, complete valid caregiver responses.
   - 'Do Not Pay': Failed attention check, contains 'test' in text, or multiple severe contradiction flags.
   - 'Manual Review': Exactly 1 ambiguous or soft inconsistency requiring coordinator review.
   - 'Incomplete - Not Eligible': Unfinished survey.
5. Excel Output Polish:
   - Rename raw REDCap field names into clean, stakeholder-friendly titles.
   - Frozen headers & auto-adjusted column widths.
"""

from __future__ import annotations

import os
import re
import sys
import shutil
from pathlib import Path
from typing import Optional, Any

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import refined_sources as sources
import refined_screening as screening

STUDY_NAMES: dict[int, str] = {
    4797: "Study 1 - Infant Autism Screening [CAN Registry]",
    4581: "Study 2 - old-Infant Autism Screening [Global Online]",
    5749: "Study 3 - Bilingual Infant Autism Screening [Prisma]",
    4931: "Study 4 - Infant Autism Screening [ICIS Board]",
}

POPULATION_MAP: dict[int, str] = {
    4797: "CAN Registry (Confirmed Real Reference)",
    4581: "Global Online Recruitment (Mixed Integrity)",
    5749: "Prisma Health Bilingual (Prospective Wave)",
    4931: "ICIS Clinical Board (Confirmed Real)",
}

ATTENTION_OPTIONS: dict[int, str] = {
    1: "Flips on a light",
    2: "Puts on a Band-Aid",
    3: "Jumps in the pool",
    4: "Tells them to do their homework",
    5: "Turns on a fan",
    6: "Takes to the ER immediately",
    7: "I don't know",
    8: "There is no image",
    9: "Prefer not to answer",
}

EDUCATION_MAP: dict[str, str] = {
    "1": "Less than 8th grade",
    "2": "9th-11th grade",
    "3": "High school diploma or GED",
    "4": "Some college courses",
    "5": "Trade or Vocational School",
    "6": "Associates or 2-year College Degree",
    "7": "Bachelor's degree",
    "8": "Master's degree",
    "9": "Professional degree (MD, PhD, JD)",
    "10": "Doctorate degree (PhD, EdD)",
}

EMPLOYMENT_MAP: dict[str, str] = {
    "1": "Employed (full-time or part-time)",
    "2": "Stay-at-home caregiver",
    "3": "Student",
    "4": "On maternity/paternity leave",
    "5": "None of the above / Other",
}

AREA_MAP: dict[str, str] = {
    "1": "Rural/Country (small town, farm)",
    "2": "Suburbs (outside a big town/city)",
    "3": "City/Urban (lots of people/buildings)",
    "4": "Other",
}


def build_simplified_records(sources_bundle: sources.RefinedSources) -> pd.DataFrame:
    """Build the clean consolidated records table with decision status and rule violations."""
    scored = screening.score_refined_records(sources_bundle)
    raw = sources_bundle.records

    rows = []
    for idx in range(len(scored)):
        s_row = scored.iloc[idx]
        r_row = raw.iloc[idx]

        pid = int(s_row["REDCap PID"])
        rid = str(s_row["Record ID"])
        study_name = STUDY_NAMES.get(pid, f"Study PID {pid}")

        # 1. Attention Check
        att_avail = int(s_row.get("Attention check available", 0))
        att_ans = int(s_row.get("Attention check answered", 0))
        r13 = int(s_row.get("R13", 0))
        failed_attention = (att_avail == 1 and att_ans == 1 and r13 == 1)

        # 2. Demographic Contradiction
        r14 = int(s_row.get("R14", 0))
        p_val = str(s_row.get("Pronoun response", "")).strip()
        g_val = str(s_row.get("Gender response", "")).strip()

        # 3. Clinical Contradiction
        r15 = int(s_row.get("R15", 0))

        # 4. Family logic
        r8 = int(s_row.get("R8", 0))

        # 5. Speed floors
        r1 = int(s_row.get("R1", 0))
        r2 = int(s_row.get("R2", 0))
        extreme_fast = (r1 == 1 and r2 == 1)

        # 6. Survey completion & contact
        completed = (s_row.get("Survey finished") == "Yes")
        email_usable = (s_row.get("Email usable") == "Yes")
        email_val = str(s_row.get("Email address", "")).strip()

        # 7. Collect rule violations
        violations = []
        notes = []

        if failed_attention:
            violations.append("Failed Attention Check (Not Band-Aid)")
            notes.append("Failed visual attention check (selected non-Band-Aid option)")
        if r14 == 1:
            violations.append("Demographic Contradiction (Pronoun vs Gender)")
            notes.append(f"Mismatched pronouns ({p_val}) and gender ({g_val})")
        if r15 == 1:
            violations.append("Clinical Contradiction (No Disability but >=1 Autistic Child)")
            notes.append("Reported no child with disability but reported 1+ child with autism")
        if r8 == 1:
            violations.append("Family Count/Branching Inconsistency")
            notes.append("Autistic children count exceeds total children or invalid branching")
        if extreme_fast:
            violations.append("Extreme Fast Survey Speed (<11.57 min)")
            notes.append("Completed whole survey and attitudes section at non-human speed")
        elif r1 == 1 or r2 == 1:
            violations.append("Speed Floor Violation")
            notes.append("Completed survey section below minimum reading time threshold")
        if completed and not email_usable:
            violations.append("Missing/Unusable Payment Email")
            notes.append("Completed survey has missing, invalid, or temporary email domain")
        if not completed:
            violations.append("Incomplete Survey")
            notes.append("Participant dropped out before completing all survey sections")

        # 8. Decision Engine
        severe_count = sum([failed_attention, r8, extreme_fast, (1 if (r14 + r15) >= 2 else 0)])
        contradiction_count = r14 + r15

        if not completed:
            payment_status = "Incomplete - Not Eligible"
            recommendation_note = "Survey incomplete — not eligible for compensation"
        elif failed_attention or severe_count >= 1 or contradiction_count >= 2:
            payment_status = "Do Not Pay"
            recommendation_note = "Flagged as invalid/bot: " + "; ".join(notes)
        elif len(violations) == 1:
            payment_status = "Manual Review"
            recommendation_note = "Needs coordinator inspection: " + "; ".join(notes)
        elif len(violations) == 0:
            payment_status = "Pay Now"
            recommendation_note = "Cleared for payment — 0 rule violations, complete valid caregiver response"
        else:
            payment_status = "Do Not Pay"
            recommendation_note = "Flagged for multiple rule violations: " + "; ".join(notes)

        # Attention check response string
        if att_avail == 0:
            att_str = "N/A - Not in survey version"
        elif att_ans == 0:
            att_str = "Not answered"
        else:
            chosen = []
            for opt_num in range(1, 10):
                if str(r_row.get(f"elig_knee___{opt_num}", "")).strip() == "1":
                    chosen.append(ATTENTION_OPTIONS.get(opt_num, f"Option {opt_num}"))
            att_str = ", ".join(chosen) if chosen else "None selected"

        # Demographics / Clinical strings
        edu_code = str(r_row.get("demo_cg1education", "")).strip()
        edu_str = EDUCATION_MAP.get(edu_code, edu_code if edu_code else "Not answered")

        emp_code = str(r_row.get("demo_cg1employment", "")).strip()
        emp_str = EMPLOYMENT_MAP.get(emp_code, emp_code if emp_code else "Not answered")

        area_code = str(r_row.get("demo_area", "")).strip()
        area_str = AREA_MAP.get(area_code, area_code if area_code else "Not answered")

        num_kids = str(r_row.get("fif_num_children", "")).strip()
        num_kids_str = num_kids if num_kids else ("1" if str(s_row.get("Children maximum", "")) else "Not answered")

        num_aut = str(r_row.get("fif_num_autistic", "")).strip()
        num_aut_str = num_aut if num_aut else ("0" if str(s_row.get("Autistic children minimum", "")) == "0.0" else "Not answered")

        needs = str(r_row.get("fif_child_needs", "")).strip()
        needs_str = "Yes" if needs == "1" else ("No" if needs == "0" else "Not answered")

        rows.append({
            "Study_Name": study_name,
            "PID": pid,
            "Record_ID": rid,
            "Payment_Status": payment_status,
            "Rule_Violations": " | ".join(violations) if violations else "None",
            "Payment_Recommendation_Notes": recommendation_note,
            "Total_Violations_Count": len(violations) if completed else max(0, len(violations) - 1),
            "Survey_Completed": "Yes" if completed else "No",
            "Total_Survey_Time_Min": round(float(s_row["Whole survey minutes"]), 2) if pd.notna(s_row.get("Whole survey minutes")) and str(s_row.get("Whole survey minutes", "")).strip() != "" else "",
            "TFA_Section_Time_Min": round(float(s_row["Thoughts and feelings minutes"]), 2) if pd.notna(s_row.get("Thoughts and feelings minutes")) and str(s_row.get("Thoughts and feelings minutes", "")).strip() != "" else "",
            "Email_Address": email_val,
            "Email_Usable": "Yes" if email_usable else "No",
            "Attention_Check_Response": att_str,
            "Caregiver_Pronouns": p_val or "Not answered",
            "Caregiver_Gender": g_val or "Not answered",
            "Number_of_Children": num_kids_str,
            "Autistic_Children_Count": num_aut_str,
            "Children_Special_Needs": needs_str,
            "Highest_Education_Level": edu_str,
            "Employment_Status": emp_str,
            "Home_ZIP_Code": str(s_row.get("ZIP provided", "")).strip(),
            "Living_Area": area_str,
            "Caregiver_Age": str(s_row.get("Reported caregiver age", "")).strip(),
            "Started_Timestamp": str(s_row.get("Started", "")).strip(),
            "Completed_Timestamp": str(s_row.get("Last section handed in", "")).strip(),
            "Contains_Test_Word": "No",
        })

    return pd.DataFrame(rows)


def build_summary_table(df: pd.DataFrame, exclusions: pd.DataFrame, provenance: pd.DataFrame) -> pd.DataFrame:
    """Build executive summary of analyzed vs excluded records and payment decisions."""
    summary_rows = []
    pid_col = "PID" if "PID" in df.columns else ("REDCap PID" if "REDCap PID" in df.columns else "project_id")
    status_col = "Payment Status" if "Payment Status" in df.columns else "Payment_Status"

    for pid in [4797, 4581, 5749, 4931]:
        sub = df[df[pid_col] == pid]
        ex_sub = exclusions[exclusions["project_id"].astype(int) == pid] if not exclusions.empty else pd.DataFrame()
        prov_sub = provenance[provenance["project_id"].astype(int) == pid] if not provenance.empty else pd.DataFrame()

        raw_count = int(prov_sub["raw_records"].iloc[0]) if not prov_sub.empty else len(sub) + len(ex_sub)
        ex_count = len(ex_sub)
        analyzed_count = len(sub)

        pay_now_cnt = int((sub[status_col] == "Pay Now").sum())
        review_cnt = int((sub[status_col] == "Manual Review").sum())
        dnp_cnt = int((sub[status_col] == "Do Not Pay").sum())
        inc_cnt = int((sub[status_col] == "Incomplete - Not Eligible").sum())

        pct_approved = f"{(pay_now_cnt / analyzed_count * 100):.1f}%" if analyzed_count > 0 else "0.0%"

        summary_rows.append({
            "Study Name": STUDY_NAMES.get(pid, f"Study {pid}"),
            "REDCap PID": pid,
            "Recruitment Population": POPULATION_MAP.get(pid, ""),
            "Total Source Records": raw_count,
            "Hard Excluded Records": ex_count,
            "Analyzed Records": analyzed_count,
            "Pay Now (Approved)": pay_now_cnt,
            "Manual Review": review_cnt,
            "Do Not Pay (Flagged Bots)": dnp_cnt,
            "Incomplete - Not Eligible": inc_cnt,
            "Approval Rate (% Analyzed)": pct_approved,
        })

    summary_df = pd.DataFrame(summary_rows)

    # Totals row
    total_raw = summary_df["Total Source Records"].sum()
    total_ex = summary_df["Hard Excluded Records"].sum()
    total_analyzed = summary_df["Analyzed Records"].sum()
    total_pay = summary_df["Pay Now (Approved)"].sum()
    total_review = summary_df["Manual Review"].sum()
    total_dnp = summary_df["Do Not Pay (Flagged Bots)"].sum()
    total_inc = summary_df["Incomplete - Not Eligible"].sum()
    total_pct = f"{(total_pay / total_analyzed * 100):.1f}%" if total_analyzed > 0 else "0.0%"

    totals_row = pd.DataFrame([{
        "Study Name": "All Studies (Combined Total)",
        "REDCap PID": "All",
        "Recruitment Population": "Full Multi-Study Cohort",
        "Total Source Records": total_raw,
        "Hard Excluded Records": total_ex,
        "Analyzed Records": total_analyzed,
        "Pay Now (Approved)": total_pay,
        "Manual Review": total_review,
        "Do Not Pay (Flagged Bots)": total_dnp,
        "Incomplete - Not Eligible": total_inc,
        "Approval Rate (% Analyzed)": total_pct,
    }])

    return pd.concat([summary_df, totals_row], ignore_index=True)


def build_rule_guide() -> pd.DataFrame:
    """Document decision engine criteria and contradiction rules."""
    rules = [
        {
            "Rule Name": "Attention Check ('Band-Aid')",
            "Field(s)": "elig_knee",
            "Logic & Threshold": "Flags any answer other than Option 2 ('Puts on a Band-Aid'). Option 6 (ER), Option 5 (fan), Option 1 (light), Option 3 (pool), Option 4 (homework) are flagged.",
            "Weight / Severity": "Hard Immediate Failure",
            "Decision Impact": "Do Not Pay (Confirmed Bot / Careless Responder)",
        },
        {
            "Rule Name": "Demographic Contradiction (Pronoun vs Gender)",
            "Field(s)": "pronouns_elig, demo_gender",
            "Logic & Threshold": "Flags contradictory profiles: Pronouns='She/Her' with Gender='Man', or Pronouns='He/Him' with Gender='Woman'.",
            "Weight / Severity": "Contradiction Flag",
            "Decision Impact": "1 violation -> Manual Review; paired with another contradiction or speed flag -> Do Not Pay",
        },
        {
            "Rule Name": "Clinical Contradiction (Disability vs Autism)",
            "Field(s)": "fif_child_needs, fif_num_autistic",
            "Logic & Threshold": "Flags explicit selection of 'No disability/special needs' (fif_child_needs=0) paired with reporting >=1 autistic child (fif_num_autistic>=1).",
            "Weight / Severity": "Contradiction Flag",
            "Decision Impact": "1 violation -> Manual Review; paired with another contradiction or speed flag -> Do Not Pay",
        },
        {
            "Rule Name": "Family Count Inconsistency",
            "Field(s)": "fif_num_autistic, fif_num_children",
            "Logic & Threshold": "Flags records where autistic children count strictly exceeds reported total number of children, or gated follow-ups answered with 0 autistic children.",
            "Weight / Severity": "Hard Logic Failure",
            "Decision Impact": "Do Not Pay",
        },
        {
            "Rule Name": "Extreme Fast Survey Completion",
            "Field(s)": "get_time_fif, get_time_val, get_time_tfa, get_time_demo",
            "Logic & Threshold": "Total survey completion time < 11.57 minutes AND Thoughts & Feelings section < 7.85 minutes.",
            "Weight / Severity": "Severe Speed Flag",
            "Decision Impact": "Do Not Pay (Impossible human reading comprehension rate)",
        },
        {
            "Rule Name": "Speed Floor Violation",
            "Field(s)": "Section completion timers",
            "Logic & Threshold": "Individual survey section completed below minimum speed floor (FIF <2.55 min, Values <0.22 min, TFA <6.67 min, Demographics <0.95 min).",
            "Weight / Severity": "Soft Timing Flag",
            "Decision Impact": "Manual Review (Inspect text answers and consistency)",
        },
        {
            "Rule Name": "Universal 'Test' Word Exclusion",
            "Field(s)": "All free-text / notes fields across all instruments",
            "Logic & Threshold": "Searches for standalone word 'test' (case-insensitive word boundary). Pre-screened and excluded.",
            "Weight / Severity": "Hard Exclusion",
            "Decision Impact": "Excluded Records (Do Not Pay)",
        },
        {
            "Rule Name": "PID 5749 Clones & Pre-Launch Exclusion",
            "Field(s)": "record_id (PID 5749)",
            "Logic & Threshold": "PID 5749 records 1-445 are copied archive clones from PID 4700; records 446-473 are pre-launch tests. Records >=474 are valid prospective submissions.",
            "Weight / Severity": "Cohort Partitioning",
            "Decision Impact": "Excluded Records (Retained in archive/protocol)",
        },
    ]
    return pd.DataFrame(rules)


def format_excel_workbook(
    output_path: Path,
    summary_df: pd.DataFrame,
    all_responses_df: pd.DataFrame,
    pay_now_df: pd.DataFrame,
    manual_review_df: pd.DataFrame,
    do_not_pay_df: pd.DataFrame,
    excluded_df: pd.DataFrame,
    rule_guide_df: pd.DataFrame,
) -> None:
    """Write and style the multi-sheet workbook with frozen headers and auto-column widths."""
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Executive Summary", index=False)
        all_responses_df.to_excel(writer, sheet_name="Simplified All Responses", index=False)
        pay_now_df.to_excel(writer, sheet_name="Pay Now (Approved)", index=False)
        manual_review_df.to_excel(writer, sheet_name="Manual Review Queue", index=False)
        do_not_pay_df.to_excel(writer, sheet_name="Do Not Pay (Flagged Bots)", index=False)
        excluded_df.to_excel(writer, sheet_name="Excluded Records", index=False)
        rule_guide_df.to_excel(writer, sheet_name="Decision & Rule Guide", index=False)

    # Open with openpyxl to apply rich styling, colors, freeze panes, auto widths
    wb = openpyxl.load_workbook(output_path)

    # Styles
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

        # Freeze panes & cell styling
        if sheetname in ["Simplified All Responses", "Pay Now (Approved)", "Manual Review Queue", "Do Not Pay (Flagged Bots)"]:
            ws.freeze_panes = "E2"  # Freezes row 1 and columns A-D (Study Name, PID, Record ID, Payment Status)

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
                elif "Incomplete" in status_val:
                    status_cell.fill = inc_fill
                    status_cell.font = inc_font

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
                    if c_idx >= 4:
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

        elif sheetname == "Decision & Rule Guide":
            ws.freeze_panes = "A2"
            for row_idx in range(2, ws.max_row + 1):
                for c_idx in range(1, ws.max_column + 1):
                    c = ws.cell(row=row_idx, column=c_idx)
                    c.border = thin_border
                    c.alignment = Alignment(vertical="top", wrap_text=True)

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col[:100]:  # sample up to 100 rows for speed
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            adjusted_width = min(65, max(12, max_len + 3))
            ws.column_dimensions[col_letter].width = adjusted_width

    wb.save(output_path)


def generate_simplified_all_responses_excel(
    project_dir: Path | str = PROJECT_DIR,
    refresh: bool = True,
    output_path: Optional[Path | str] = None,
) -> Path:
    """Main pipeline execution function to produce 'Simplified all responses.xlsx'."""
    project_dir = Path(project_dir).resolve()

    if output_path is None:
        target_dir = project_dir / "Caregiver Outputs"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / "Simplified all responses.xlsx"
    else:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading REDCap sources (refresh={refresh})...")
    src = sources.load_refined_sources(project_dir, refresh=refresh)

    print("Building simplified response records...")
    all_responses_df = build_simplified_records(src)

    # Rename columns to clean, stakeholder-friendly titles
    rename_map = {
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
    all_responses_df = all_responses_df.rename(columns=rename_map)

    # Filtered sheets
    pay_now_df = all_responses_df[all_responses_df["Payment Status"] == "Pay Now"].copy()
    manual_review_df = all_responses_df[all_responses_df["Payment Status"] == "Manual Review"].copy()
    do_not_pay_df = all_responses_df[all_responses_df["Payment Status"] == "Do Not Pay"].copy()

    # Excluded records sheet
    ex_df = src.exclusions.copy()
    if not ex_df.empty:
        ex_df["Study Name"] = ex_df["project_id"].astype(int).map(STUDY_NAMES)
        ex_df["PID"] = ex_df["project_id"]
        ex_df["Record ID"] = ex_df["record_id"]
        ex_df["Exclusion Reason"] = ex_df["exclusion_reason"]
        ex_df["Matched Text Fields"] = ex_df["matched_text_fields"]
        ex_df["Payment Status"] = "Excluded - Do Not Pay"
        ex_clean_df = ex_df[["Study Name", "PID", "Record ID", "Payment Status", "Exclusion Reason", "Matched Text Fields"]]
    else:
        ex_clean_df = pd.DataFrame(columns=["Study Name", "PID", "Record ID", "Payment Status", "Exclusion Reason", "Matched Text Fields"])

    # Summary table & rule guide
    summary_df = build_summary_table(all_responses_df.rename(columns={"REDCap PID": "PID"}), src.exclusions, src.provenance)
    rule_guide_df = build_rule_guide()

    print(f"Writing and styling workbook to: {output_path}...")
    format_excel_workbook(
        output_path=output_path,
        summary_df=summary_df,
        all_responses_df=all_responses_df,
        pay_now_df=pay_now_df,
        manual_review_df=manual_review_df,
        do_not_pay_df=do_not_pay_df,
        excluded_df=ex_clean_df,
        rule_guide_df=rule_guide_df,
    )

    # Also copy to root project directory for quick stakeholder access
    root_copy = project_dir / "Simplified all responses.xlsx"
    try:
        shutil.copy2(output_path, root_copy)
        print(f"Also created copy at: {root_copy}")
    except Exception as e:
        print(f"Notice: could not copy to {root_copy}: {e}")

    print(f"Successfully generated 'Simplified all responses.xlsx' ({os.path.getsize(output_path):,} bytes).")
    return output_path


if __name__ == "__main__":
    generate_simplified_all_responses_excel(refresh=True)
