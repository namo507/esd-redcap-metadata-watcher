#!/usr/bin/env python3
"""
generate_demographic_excel.py
Generates a polished, multi-tab Excel workbook containing detailed demographic
breakdowns, counts, percentages, and percentage point comparisons across
REDCap caregiver studies.
"""

import os
import shutil
import pandas as pd
import numpy as np
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Paths
INPUT_EXCEL = '/Users/namomac/esd-redcap-metadata-watcher/projects/caregiver-cluster-analysis/Caregiver Outputs/Simplified all responses.xlsx'
OUTPUT_DIR = '/Users/namomac/esd-redcap-metadata-watcher/projects/caregiver-cluster-analysis/Caregiver Outputs'
OUTPUT_EXCEL = os.path.join(OUTPUT_DIR, 'Demographic Analysis and Comparisons.xlsx')
ROOT_COPY = '/Users/namomac/esd-redcap-metadata-watcher/projects/caregiver-cluster-analysis/Demographic Analysis and Comparisons.xlsx'

# Load data
df = pd.read_excel(INPUT_EXCEL, sheet_name='Simplified All Responses')
comp = df[df['Survey Completed'] == 'Yes'].copy()

# Sample sizes
n_4797 = int((comp['REDCap PID'] == 4797).sum())
n_4581 = int((comp['REDCap PID'] == 4581).sum())
n_4931 = int((comp['REDCap PID'] == 4931).sum())
n_prior = n_4797 + n_4581 + n_4931
n_5749 = int((comp['REDCap PID'] == 5749).sum())

# Styling constants
FONT_TITLE = Font(name='Calibri', size=16, bold=True, color='1E3A8A')
FONT_SUBTITLE = Font(name='Calibri', size=11, italic=True, color='475569')
FONT_SECTION = Font(name='Calibri', size=13, bold=True, color='1E293B')
FONT_HEADER = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
FONT_DATA = Font(name='Calibri', size=10, color='0F172A')
FONT_DATA_BOLD = Font(name='Calibri', size=10, bold=True, color='0F172A')
FONT_TOTAL = Font(name='Calibri', size=10, bold=True, color='1E3A8A')

FILL_HEADER_NAVY = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid') # Deep Navy
FILL_HEADER_RECENT = PatternFill(start_color='0284C7', end_color='0284C7', fill_type='solid') # Bright Sky Blue
FILL_HEADER_DIFF = PatternFill(start_color='0F766E', end_color='0F766E', fill_type='solid') # Deep Teal
FILL_RECENT_DATA = PatternFill(start_color='F0F9FF', end_color='F0F9FF', fill_type='solid') # Soft blue tint
FILL_ZEBRA = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid') # Very light slate
FILL_TOTAL = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid') # Slate 100

BORDER_THIN = Border(
    left=Side(style='thin', color='CBD5E1'),
    right=Side(style='thin', color='CBD5E1'),
    top=Side(style='thin', color='CBD5E1'),
    bottom=Side(style='thin', color='CBD5E1')
)
BORDER_TOTAL = Border(
    left=Side(style='thin', color='CBD5E1'),
    right=Side(style='thin', color='CBD5E1'),
    top=Side(style='thin', color='1E3A8A'),
    bottom=Side(style='double', color='1E3A8A')
)

ALIGN_LEFT = Alignment(horizontal='left', vertical='center')
ALIGN_CENTER = Alignment(horizontal='center', vertical='center')
ALIGN_RIGHT = Alignment(horizontal='right', vertical='center')
ALIGN_HEADER = Alignment(horizontal='center', vertical='center', wrap_text=True)

NUM_INT = '#,##0'
NUM_PCT = '0.0%'
NUM_DIFF = '+0.0" pp";-0.0" pp";0.0" pp"'

# Helper to compute breakdown rows
def compute_breakdown(series, cat_list=None):
    if cat_list is None:
        cat_list = series.dropna().unique().tolist()
        try:
            cat_list = sorted(cat_list)
        except:
            pass
            
    rows = []
    for cat in cat_list:
        c_4797 = int(((comp['REDCap PID'] == 4797) & (series == cat)).sum())
        p_4797 = c_4797 / n_4797 if n_4797 > 0 else 0.0
        
        c_4581 = int(((comp['REDCap PID'] == 4581) & (series == cat)).sum())
        p_4581 = c_4581 / n_4581 if n_4581 > 0 else 0.0
        
        c_4931 = int(((comp['REDCap PID'] == 4931) & (series == cat)).sum())
        p_4931 = c_4931 / n_4931 if n_4931 > 0 else 0.0
        
        c_prior = c_4797 + c_4581 + c_4931
        p_prior = c_prior / n_prior if n_prior > 0 else 0.0
        
        c_5749 = int(((comp['REDCap PID'] == 5749) & (series == cat)).sum())
        p_5749 = c_5749 / n_5749 if n_5749 > 0 else 0.0
        
        diff_4581 = (p_5749 - p_4581) * 100 # In percentage points
        diff_prior = (p_5749 - p_prior) * 100
        
        rows.append({
            'Category': str(cat),
            '4797_N': c_4797, '4797_P': p_4797,
            '4581_N': c_4581, '4581_P': p_4581,
            '4931_N': c_4931, '4931_P': p_4931,
            'Prior_N': c_prior, 'Prior_P': p_prior,
            '5749_N': c_5749, '5749_P': p_5749,
            'Diff_4581': diff_4581,
            'Diff_Prior': diff_prior
        })
    return rows

# Create openpyxl Workbook
wb = openpyxl.Workbook()
wb.remove(wb.active) # Remove default sheet

# Standard headers definition
STD_HEADERS = [
    ("Category / Attribute", ALIGN_LEFT, 28, FILL_HEADER_NAVY),
    ("CAN Registry\n(4797) Count", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("CAN Registry\n(4797) %", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("Global Online\n(4581) Count", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("Global Online\n(4581) %", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("ICIS Board\n(4931) Count", ALIGN_RIGHT, 13, FILL_HEADER_NAVY),
    ("ICIS Board\n(4931) %", ALIGN_RIGHT, 13, FILL_HEADER_NAVY),
    ("Prior Combined\n(Historical) Count", ALIGN_RIGHT, 16, FILL_HEADER_NAVY),
    ("Prior Combined\n(Historical) %", ALIGN_RIGHT, 16, FILL_HEADER_NAVY),
    ("★ Recent Wave\n(5749 446+) Count", ALIGN_RIGHT, 16, FILL_HEADER_RECENT),
    ("★ Recent Wave\n(5749 446+) %", ALIGN_RIGHT, 16, FILL_HEADER_RECENT),
    ("Diff vs 4581\nBaseline (pp)", ALIGN_RIGHT, 15, FILL_HEADER_DIFF),
    ("Diff vs Prior\nCombined (pp)", ALIGN_RIGHT, 15, FILL_HEADER_DIFF),
    ("Shift Finding / Diversification Trend", ALIGN_LEFT, 32, FILL_HEADER_NAVY),
]

def render_table(ws, start_row, title, rows, total_label=None, finding_map=None):
    # Section title
    ws.cell(row=start_row, column=1, value=title).font = FONT_SECTION
    start_row += 1
    
    # Header Row
    header_row = start_row
    ws.row_dimensions[header_row].height = 28
    for col_idx, (h_name, align, width, fill) in enumerate(STD_HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h_name)
        cell.font = FONT_HEADER
        cell.fill = fill
        cell.alignment = ALIGN_HEADER
        cell.border = BORDER_THIN
    
    curr_row = header_row + 1
    
    # Data Rows
    tot_4797 = 0
    tot_4581 = 0
    tot_4931 = 0
    tot_prior = 0
    tot_5749 = 0
    
    for idx, r in enumerate(rows):
        is_zebra = (idx % 2 == 1)
        ws.row_dimensions[curr_row].height = 20
        
        tot_4797 += r['4797_N']
        tot_4581 += r['4581_N']
        tot_4931 += r['4931_N']
        tot_prior += r['Prior_N']
        tot_5749 += r['5749_N']
        
        finding = finding_map.get(r['Category'], '') if finding_map else ''
        
        values = [
            (r['Category'], FONT_DATA, ALIGN_LEFT, None, None),
            (r['4797_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4797_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['4581_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4581_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['4931_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4931_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['Prior_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['Prior_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['5749_N'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_INT, FILL_RECENT_DATA),
            (r['5749_P'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_PCT, FILL_RECENT_DATA),
            (r['Diff_4581'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_DIFF, None),
            (r['Diff_Prior'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_DIFF, None),
            (finding, FONT_DATA, ALIGN_LEFT, None, None),
        ]
        
        for c_idx, (val, font, align, num_fmt, cell_fill) in enumerate(values, start=1):
            cell = ws.cell(row=curr_row, column=c_idx, value=val)
            cell.font = font
            cell.alignment = align
            cell.border = BORDER_THIN
            if num_fmt:
                cell.number_format = num_fmt
            if cell_fill:
                cell.fill = cell_fill
            elif is_zebra:
                cell.fill = FILL_ZEBRA
                
        curr_row += 1
        
    # Optional Total Row
    if total_label:
        ws.row_dimensions[curr_row].height = 22
        p_tot_4797 = tot_4797 / n_4797 if n_4797 > 0 else 0.0
        p_tot_4581 = tot_4581 / n_4581 if n_4581 > 0 else 0.0
        p_tot_4931 = tot_4931 / n_4931 if n_4931 > 0 else 0.0
        p_tot_prior = tot_prior / n_prior if n_prior > 0 else 0.0
        p_tot_5749 = tot_5749 / n_5749 if n_5749 > 0 else 0.0
        
        tot_values = [
            (total_label, FONT_TOTAL, ALIGN_LEFT, None),
            (tot_4797, FONT_TOTAL, ALIGN_RIGHT, NUM_INT),
            (p_tot_4797, FONT_TOTAL, ALIGN_RIGHT, NUM_PCT),
            (tot_4581, FONT_TOTAL, ALIGN_RIGHT, NUM_INT),
            (p_tot_4581, FONT_TOTAL, ALIGN_RIGHT, NUM_PCT),
            (tot_4931, FONT_TOTAL, ALIGN_RIGHT, NUM_INT),
            (p_tot_4931, FONT_TOTAL, ALIGN_RIGHT, NUM_PCT),
            (tot_prior, FONT_TOTAL, ALIGN_RIGHT, NUM_INT),
            (p_tot_prior, FONT_TOTAL, ALIGN_RIGHT, NUM_PCT),
            (tot_5749, FONT_TOTAL, ALIGN_RIGHT, NUM_INT),
            (p_tot_5749, FONT_TOTAL, ALIGN_RIGHT, NUM_PCT),
            (0.0, FONT_TOTAL, ALIGN_RIGHT, NUM_DIFF),
            (0.0, FONT_TOTAL, ALIGN_RIGHT, NUM_DIFF),
            ("100% Cohort Total", FONT_TOTAL, ALIGN_LEFT, None),
        ]
        for c_idx, (val, font, align, num_fmt) in enumerate(tot_values, start=1):
            cell = ws.cell(row=curr_row, column=c_idx, value=val)
            cell.font = font
            cell.alignment = align
            cell.border = BORDER_TOTAL
            cell.fill = FILL_TOTAL
            if num_fmt:
                cell.number_format = num_fmt
        curr_row += 1

    return curr_row + 2 # Leave 2 empty rows after table

# ==========================================
# 1. TAB: EXECUTIVE SUMMARY
# ==========================================
ws_exec = wb.create_sheet(title='Executive Summary')
ws_exec.views.sheetView[0].showGridLines = True

ws_exec.cell(row=2, column=2, value="ESD Lab Caregiver Studies — Multi-Study Demographic Comparison").font = FONT_TITLE
ws_exec.cell(row=3, column=2, value="Evaluation of Recent Community & Clinical Recruitment (Study 3 / PID 5749, Records 446+) vs. Historical Online Baseline (PID 4581)").font = FONT_SUBTITLE

# Table 1: Study Sample Sizes
ws_exec.cell(row=5, column=2, value="1. Study Sample Sizes & Survey Completion").font = FONT_SECTION
headers_samples = ["REDCap PID", "Study Name", "Recruitment Setting & Target", "Total Started", "Completed N", "Completion Rate", "Role in Analysis"]
ws_exec.row_dimensions[6].height = 25
for col_idx, h in enumerate(headers_samples, start=2):
    cell = ws_exec.cell(row=6, column=col_idx, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER_NAVY
    cell.alignment = ALIGN_HEADER
    cell.border = BORDER_THIN

sample_data = [
    (4797, "Study 1 — CAN Registry", "Confirmed real community registry (SC local)", 113, 67, 67/113, "Reference Group (Real)"),
    (4581, "Study 2 — Global Online", "Open internet recruitment (Heavy bot contamination)", 1518, 1320, 1320/1518, "Historical Baseline"),
    (4931, "Study 4 — ICIS Board", "Clinical expert advisory board members", 6, 4, 4/6, "Clinical Advisory"),
    ("Combined", "Prior Studies Combined", "Total historical baseline pool (4797 + 4581 + 4931)", 1637, 1391, 1391/1637, "Historical Combined Pool"),
    (5749, "★ Study 3 — Bilingual Prisma (446+)", "Recent targeted clinical & bilingual outreach wave", 55, 24, 24/55, "Recent Target Cohort"),
]

for row_idx, r in enumerate(sample_data, start=7):
    ws_exec.row_dimensions[row_idx].height = 20
    is_recent = (r[0] == 5749)
    for c_idx, val in enumerate(r, start=2):
        cell = ws_exec.cell(row=row_idx, column=c_idx, value=val)
        cell.border = BORDER_THIN
        cell.font = FONT_DATA_BOLD if is_recent else FONT_DATA
        if is_recent:
            cell.fill = FILL_RECENT_DATA
        if c_idx in [2, 5, 6]:
            cell.alignment = ALIGN_RIGHT
            if c_idx == 7:
                cell.number_format = NUM_PCT
            elif c_idx in [5, 6]:
                cell.number_format = NUM_INT if isinstance(val, int) else None
        else:
            cell.alignment = ALIGN_LEFT

# Table 2: Diversification Scorecard
ws_exec.cell(row=14, column=2, value="2. Diversification Scorecard: Recent Wave (5749) vs. Baseline (4581)").font = FONT_SECTION
headers_score = ["Demographic Dimension", "Global Online (4581) Baseline", "Prior Studies Combined", "★ Recent Wave (5749 446+)", "Net Shift (Δ pp)", "Diversification Verdict & Analytical Meaning"]
ws_exec.row_dimensions[15].height = 25
for col_idx, h in enumerate(headers_score, start=2):
    cell = ws_exec.cell(row=15, column=col_idx, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER_RECENT if '5749' in h else (FILL_HEADER_DIFF if 'Shift' in h else FILL_HEADER_NAVY)
    cell.alignment = ALIGN_HEADER
    cell.border = BORDER_THIN

score_data = [
    ("High School Diploma or Less", 0.026, 0.026, 0.333, 30.7, "✅ Major Socioeconomic Diversification (13× higher than online baseline)"),
    ("Trade / Vocational / Some College", 0.064, 0.068, 0.292, 22.8, "✅ Expanded Non-4-Year College Population (4.3× higher)"),
    ("4-Year Bachelor's Degree Overrepresentation", 0.601, 0.584, 0.208, -39.2, "✅ De-clustering away from upper-middle class tech-savvy bias"),
    ("Rural / Country Living Area", 0.069, 0.078, 0.458, 38.9, "✅ Reaching rural & underserved non-urban communities (6.6× higher)"),
    ("South Carolina Local Residency (ZIP)", 0.066, 0.109, 0.875, 80.9, "✅ Genuine local clinical outreach in targeted SC communities"),
    ("Stay-at-Home Primary Caregivers", 0.155, 0.162, 0.417, 26.1, "✅ Substantial increase in full-time caregivers (2.7× higher)"),
    ("Child with Special Needs / Disability", 0.248, 0.258, 0.458, 21.1, "✅ Strong clinical alignment; closely mirrors verified CAN Registry (46.3%)"),
    ("Large Families (3+ Children)", 0.108, 0.121, 0.583, 47.6, "✅ Reaching larger family networks typical of community clinics (5.4× higher)"),
    ("Pay Now Rate (Clean Verified Data)", 0.249, 0.280, 0.708, 45.9, "✅ 89% reduction in bot fraud; dramatic increase in real human responses"),
]

for row_idx, r in enumerate(score_data, start=16):
    ws_exec.row_dimensions[row_idx].height = 20
    is_zebra = (row_idx % 2 == 1)
    for c_idx, val in enumerate(r, start=2):
        cell = ws_exec.cell(row=row_idx, column=c_idx, value=val)
        cell.border = BORDER_THIN
        cell.font = FONT_DATA
        if is_zebra:
            cell.fill = FILL_ZEBRA
        if c_idx in [3, 4, 5]:
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_PCT
            if c_idx == 5:
                cell.font = FONT_DATA_BOLD
                cell.fill = FILL_RECENT_DATA
        elif c_idx == 6:
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_DIFF
            cell.font = FONT_DATA_BOLD
        else:
            cell.alignment = ALIGN_LEFT

# Table 3: Narrative summary notes
ws_exec.cell(row=27, column=2, value="3. Executive Conclusions").font = FONT_SECTION
notes = [
    "1. Successful Diversification: The recent recruitment strategy (PID 5749 prospective wave, records 446+) has definitively succeeded in attracting a distinct, demographically diverse population.",
    "2. Socioeconomic Shift: In the prior online cohort (4581), 82.9% held a bachelor's or graduate degree. In the recent wave, 70.8% of respondents have a non-4-year degree background (high school, trade school, or vocational).",
    "3. Geographic Reach: Prior cohorts were overwhelmingly urban (72.9%) and scattered nationwide (85.3% outside SC). The recent cohort is 87.5% South Carolina residents, with 45.8% residing in rural communities.",
    "4. Clinical Relevance: The 45.8% disability/special needs rate closely matches the verified CAN Registry (46.3%), proving genuine clinical patient engagement.",
    "5. Data Quality: Verified 'Pay Now' rate reached 70.8% with an 89% reduction in fraudulent bot activity compared to the open web survey."
]
for idx, note in enumerate(notes, start=28):
    ws_exec.row_dimensions[idx].height = 19
    cell = ws_exec.cell(row=idx, column=2, value=note)
    cell.font = FONT_DATA
    cell.alignment = ALIGN_LEFT

# Auto width for executive sheet
for col in range(2, 9):
    col_letter = get_column_letter(col)
    max_len = max(len(str(ws_exec.cell(row=r, column=col).value or '')) for r in range(1, 35))
    ws_exec.column_dimensions[col_letter].width = max(max_len + 3, 14)

# ==========================================
# 2. TAB: MASTER COMPARISON TABLE
# ==========================================
ws_master = wb.create_sheet(title='Master Comparison Table')
ws_master.views.sheetView[0].showGridLines = True
ws_master.freeze_panes = 'C5'

ws_master.cell(row=2, column=1, value="Master Multi-Study Demographic Comparison Table").font = FONT_TITLE
ws_master.cell(row=3, column=1, value="Consolidated breakdown across all evaluated dimensions for completed caregiver responses").font = FONT_SUBTITLE

master_headers = [
    ("Demographic Dimension", ALIGN_LEFT, 26, FILL_HEADER_NAVY),
    ("Category / Attribute", ALIGN_LEFT, 28, FILL_HEADER_NAVY),
    ("CAN Registry\n(4797) Count", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("CAN Registry\n(4797) %", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("Global Online\n(4581) Count", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("Global Online\n(4581) %", ALIGN_RIGHT, 14, FILL_HEADER_NAVY),
    ("ICIS Board\n(4931) Count", ALIGN_RIGHT, 13, FILL_HEADER_NAVY),
    ("ICIS Board\n(4931) %", ALIGN_RIGHT, 13, FILL_HEADER_NAVY),
    ("Prior Combined\n(Historical) Count", ALIGN_RIGHT, 16, FILL_HEADER_NAVY),
    ("Prior Combined\n(Historical) %", ALIGN_RIGHT, 16, FILL_HEADER_NAVY),
    ("★ Recent Wave\n(5749 446+) Count", ALIGN_RIGHT, 16, FILL_HEADER_RECENT),
    ("★ Recent Wave\n(5749 446+) %", ALIGN_RIGHT, 16, FILL_HEADER_RECENT),
    ("Diff vs 4581\nBaseline (pp)", ALIGN_RIGHT, 15, FILL_HEADER_DIFF),
    ("Diff vs Prior\nCombined (pp)", ALIGN_RIGHT, 15, FILL_HEADER_DIFF),
    ("Shift Finding / Diversification Trend", ALIGN_LEFT, 32, FILL_HEADER_NAVY),
]

ws_master.row_dimensions[4].height = 28
for c_idx, (h_name, align, width, fill) in enumerate(master_headers, start=1):
    cell = ws_master.cell(row=4, column=c_idx, value=h_name)
    cell.font = FONT_HEADER
    cell.fill = fill
    cell.alignment = ALIGN_HEADER
    cell.border = BORDER_THIN
    ws_master.column_dimensions[get_column_letter(c_idx)].width = width

# Master rows collection
master_sections = [
    ("Education Level", comp['Highest Education Level'], [
        'High school diploma or GED', 'Trade or Vocational School', 'Some college courses',
        'Associates or 2-year College Degree', "Bachelor's degree", "Master's degree",
        'Professional degree (MD, PhD, JD)', '9th-11th grade'
    ], {
        'High school diploma or GED': '⬆ Major increase (+31.1 pp); 15× higher than online cohort',
        'Trade or Vocational School': '⬆ Major increase (+12.5 pp); vocational outreach working',
        'Some college courses': '⬆ Major increase (+10.3 pp); broadened educational diversity',
        'Associates or 2-year College Degree': '↔ Stable representation across waves (~8%)',
        "Bachelor's degree": '⬇ Sharp drop (-39.2 pp); prior online pool was 60% bachelor’s',
        "Master's degree": '⬇ Reduced overrepresentation (-13.1 pp)',
        'Professional degree (MD, PhD, JD)': '↔ Removed clinical board overrepresentation',
        '9th-11th grade': '↔ Rare in all survey cohorts'
    }),
    ("Living Area", comp['Living Area Description'], [
        'Rural/Country (small town, farm)', 'Suburbs (outside a big town/city)',
        'City/Urban (lots of people/buildings)', 'Not answered'
    ], {
        'Rural/Country (small town, farm)': '⬆ Massive rural diversification (+38.9 pp); 6.6× higher',
        'Suburbs (outside a big town/city)': '⬆ Solid representation in suburban towns (+13.6 pp)',
        'City/Urban (lots of people/buildings)': '⬇ Sharp de-clustering (-52.0 pp); reduced tech-center bias',
        'Not answered': '— Minimal missingness'
    }),
    ("Employment Status", comp['Employment Status'], [
        'Stay-at-home caregiver', 'Employed (full-time or part-time)',
        'None of the above / Other', 'On maternity/paternity leave', 'Student'
    ], {
        'Stay-at-home caregiver': '⬆ 2.7× higher (+26.1 pp); key underrepresented caregiver cohort',
        'Employed (full-time or part-time)': '⬇ Balanced workforce participation (-34.1 pp)',
        'None of the above / Other': '⬆ Captures diverse life situations (+12.3 pp)',
        'On maternity/paternity leave': '↔ Low incidence in prospective wave',
        'Student': '↔ Low incidence'
    }),
    ("Children in Household", comp['Number of Children'], [1, 2, 3, 4, 5, 7], {
        '1': '⬇ De-clustered single-child online pool (-32.3 pp)',
        '2': '↔ Balanced representation (25.0%)',
        '3': '⬆ 4× higher (+28.1 pp); reflects larger community families',
        '4': '⬆ 11× higher (+11.4 pp)',
        '5': '⬆ 36× higher (+8.1 pp)',
        '7': '— Rare outlier in registry'
    }),
    ("Child Special Needs", comp['Children Special Needs/Disability'], ['Yes', 'No'], {
        'Yes': '✅ Mirrors verified CAN Registry (46.3% vs 45.8% recent)',
        'No': '⬇ Less generic population skew (-21.1 pp)'
    }),
    ("Autistic Children Count", comp['Autistic Children Count'], [0, 1, 2, 3], {
        '0': '⬆ Broad caregiver group without autism (+34.8 pp)',
        '1': '⬇ Eliminates bot artifact (4581 bots almost universally clicked 1)',
        '2': '↔ Plausible clinical multi-child incidence (4.2%)',
        '3': '— Rare outlier'
    }),
    ("Caregiver Gender", comp['Caregiver Gender'], ['Woman', 'Man', 'Non-binary', 'Prefer not to answer'], {
        'Woman': '✅ 100% women in recent wave; mirrors primary pediatric caregivers',
        'Man': '⬇ Bot pool overrepresented men in 4581 (35.0%)',
        'Non-binary': '— Low incidence',
        'Prefer not to answer': '— Low incidence'
    }),
    ("Payment Review Status", comp['Payment Status'], ['Pay Now', 'Manual Review', 'Do Not Pay'], {
        'Pay Now': '✅ Dramatic data quality improvement (+45.9 pp approved real)',
        'Manual Review': '⬇ Borderline flags reduced to 25.0%',
        'Do Not Pay': '✅ 89% reduction in fraudulent bot activity (-33.5 pp)'
    })
]

curr_m_row = 5
for dim_name, series_data, cats, findings in master_sections:
    rows = compute_breakdown(series_data, cats)
    for idx, r in enumerate(rows):
        ws_master.row_dimensions[curr_m_row].height = 20
        is_zebra = (curr_m_row % 2 == 1)
        f_text = findings.get(r['Category'], '')
        
        m_vals = [
            (dim_name, FONT_DATA_BOLD, ALIGN_LEFT, None, None),
            (r['Category'], FONT_DATA, ALIGN_LEFT, None, None),
            (r['4797_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4797_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['4581_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4581_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['4931_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['4931_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['Prior_N'], FONT_DATA, ALIGN_RIGHT, NUM_INT, None),
            (r['Prior_P'], FONT_DATA, ALIGN_RIGHT, NUM_PCT, None),
            (r['5749_N'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_INT, FILL_RECENT_DATA),
            (r['5749_P'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_PCT, FILL_RECENT_DATA),
            (r['Diff_4581'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_DIFF, None),
            (r['Diff_Prior'], FONT_DATA_BOLD, ALIGN_RIGHT, NUM_DIFF, None),
            (f_text, FONT_DATA, ALIGN_LEFT, None, None),
        ]
        
        for c_idx, (val, font, align, num_fmt, cell_fill) in enumerate(m_vals, start=1):
            cell = ws_master.cell(row=curr_m_row, column=c_idx, value=val)
            cell.font = font
            cell.alignment = align
            cell.border = BORDER_THIN
            if num_fmt:
                cell.number_format = num_fmt
            if cell_fill:
                cell.fill = cell_fill
            elif is_zebra:
                cell.fill = FILL_ZEBRA
                
        curr_m_row += 1

# ==========================================
# 3. TAB: EDUCATION BREAKDOWN
# ==========================================
ws_edu = wb.create_sheet(title='Education Breakdown')
ws_edu.views.sheetView[0].showGridLines = True
ws_edu.freeze_panes = 'B5'
ws_edu.cell(row=2, column=1, value="Educational Attainment Breakdown & Comparisons").font = FONT_TITLE
ws_edu.cell(row=3, column=1, value="Comparing recent targeted recruitment against historical online cohorts").font = FONT_SUBTITLE

rows_edu_gran = compute_breakdown(comp['Highest Education Level'], [
    'High school diploma or GED', 'Trade or Vocational School', 'Some college courses',
    'Associates or 2-year College Degree', "Bachelor's degree", "Master's degree",
    'Professional degree (MD, PhD, JD)', '9th-11th grade'
])
findings_edu_gran = {
    'High school diploma or GED': '⬆ Major increase (+31.1 pp); 15× higher than online baseline',
    'Trade or Vocational School': '⬆ Major increase (+12.5 pp); 4× higher than online baseline',
    'Some college courses': '⬆ Major increase (+10.3 pp); broadened educational diversity',
    'Associates or 2-year College Degree': '↔ Consistent representation across cohorts (~8%)',
    "Bachelor's degree": '⬇ Reduced skew (-39.2 pp); 4581 was 60% bachelor’s degrees',
    "Master's degree": '⬇ Reduced skew (-13.1 pp); less academic clustering',
    'Professional degree (MD, PhD, JD)': '↔ Removed board-level overrepresentation',
    '9th-11th grade': '↔ Minimal incidence'
}
next_row = render_table(ws_edu, 5, "Table 1: Granular Education Level Categories", rows_edu_gran, total_label="Total Completed (100%)", finding_map=findings_edu_gran)

# Consolidated Education Tiers
def edu_tier(x):
    if x in ['9th-11th grade', 'High school diploma or GED']:
        return 'High School Diploma or Less'
    elif x in ['Trade or Vocational School', 'Some college courses']:
        return 'Vocational / Some College (No Degree)'
    elif x == 'Associates or 2-year College Degree':
        return 'Associate Degree (2-Year College)'
    elif x == "Bachelor's degree":
        return "Bachelor's Degree (4-Year College)"
    elif x in ["Master's degree", 'Professional degree (MD, PhD, JD)']:
        return 'Graduate / Professional Degree (MA/MS/PhD/MD)'
    return 'Other'

comp['Edu_Tier'] = comp['Highest Education Level'].apply(edu_tier)
rows_edu_tier = compute_breakdown(comp['Edu_Tier'], [
    'High School Diploma or Less', 'Vocational / Some College (No Degree)',
    'Associate Degree (2-Year College)', "Bachelor's Degree (4-Year College)",
    'Graduate / Professional Degree (MA/MS/PhD/MD)'
])
findings_edu_tier = {
    'High School Diploma or Less': '✅ +30.8 pp increase; substantial representation of non-college families',
    'Vocational / Some College (No Degree)': '✅ +22.8 pp increase; reached working-class caregivers',
    'Associate Degree (2-Year College)': '↔ Stable (~8%)',
    "Bachelor's Degree (4-Year College)": '⬇ De-clustering away from upper-income bias (-39.2 pp)',
    'Graduate / Professional Degree (MA/MS/PhD/MD)': '⬇ Balanced distribution (-14.5 pp)'
}
render_table(ws_edu, next_row, "Table 2: Consolidated Educational Attainment Tiers", rows_edu_tier, total_label="Total Completed (100%)", finding_map=findings_edu_tier)

for col in range(1, 15):
    ws_edu.column_dimensions[get_column_letter(col)].width = STD_HEADERS[col-1][2]

# ==========================================
# 4. TAB: GEOGRAPHY & LIVING AREA
# ==========================================
ws_geo = wb.create_sheet(title='Living Area & Geography')
ws_geo.views.sheetView[0].showGridLines = True
ws_geo.freeze_panes = 'B5'
ws_geo.cell(row=2, column=1, value="Geographic Setting & Community Distribution").font = FONT_TITLE
ws_geo.cell(row=3, column=1, value="Urban/Rural living environment and South Carolina local representation").font = FONT_SUBTITLE

rows_geo = compute_breakdown(comp['Living Area Description'], [
    'Rural/Country (small town, farm)', 'Suburbs (outside a big town/city)',
    'City/Urban (lots of people/buildings)', 'Not answered'
])
findings_geo = {
    'Rural/Country (small town, farm)': '✅ +38.9 pp; 45.8% rural vs. 6.9% in online cohort (6.6× higher)',
    'Suburbs (outside a big town/city)': '✅ +13.6 pp; 33.3% suburban representation',
    'City/Urban (lots of people/buildings)': '⬇ -52.0 pp; prior online cohort was 72.9% urban',
    'Not answered': '— Minimal missingness'
}
next_row_geo = render_table(ws_geo, 5, "Table 1: Living Area Environmental Setting", rows_geo, total_label="Total Completed (100%)", finding_map=findings_geo)

# State / Regional residency
def get_state(zip_val):
    z = str(zip_val).strip()[:3]
    if z.startswith('29'):
        return 'South Carolina (SC) Local'
    elif z.startswith('27') or z.startswith('28'):
        return 'North Carolina (NC)'
    elif z.startswith('30') or z.startswith('31'):
        return 'Georgia (GA)'
    else:
        return 'Other US States / Non-Regional'

comp['Region'] = comp['Home ZIP Code'].apply(get_state)
rows_reg = compute_breakdown(comp['Region'], [
    'South Carolina (SC) Local', 'North Carolina (NC)', 'Georgia (GA)', 'Other US States / Non-Regional'
])
findings_reg = {
    'South Carolina (SC) Local': '✅ 87.5% in recent wave; proves local hospital & clinic outreach',
    'North Carolina (NC)': '↔ Neighboring state',
    'Georgia (GA)': '↔ Neighboring state',
    'Other US States / Non-Regional': '⬇ -72.8 pp; online bot/national pool de-clustering'
}
next_row_geo2 = render_table(ws_geo, next_row_geo, "Table 2: Geographic Residency (Derived from ZIP Code)", rows_reg, total_label="Total Completed (100%)", finding_map=findings_reg)

# Table 3: ZIP Code Listing for PID 5749
ws_geo.cell(row=next_row_geo2, column=1, value="Table 3: Recent Cohort (PID 5749) South Carolina Community ZIP Codes").font = FONT_SECTION
headers_zip = ["ZIP Code", "Town / Community Area", "County / Region", "Completed Responses (N)", "% of PID 5749 Wave"]
ws_geo.row_dimensions[next_row_geo2+1].height = 25
for c_idx, h in enumerate(headers_zip, start=1):
    cell = ws_geo.cell(row=next_row_geo2+1, column=c_idx, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER_NAVY
    cell.alignment = ALIGN_HEADER
    cell.border = BORDER_THIN

zip_details = [
    (29680, "Simpsonville", "Greenville County (Upstate)", 2, 2/24),
    (29673, "Piedmont", "Greenville / Anderson County", 2, 2/24),
    (29611, "Greenville (West / Berea)", "Greenville County", 2, 2/24),
    (29016, "Blythewood", "Richland County (Midlands)", 1, 1/24),
    (29053, "Gaston", "Lexington County (Midlands)", 1, 1/24),
    (29078, "Lugoff", "Kershaw County", 1, 1/24),
    (29210, "Columbia (St. Andrews)", "Richland County", 1, 1/24),
    (29301, "Spartanburg (West)", "Spartanburg County", 1, 1/24),
    (29303, "Spartanburg (North)", "Spartanburg County", 1, 1/24),
    (29316, "Boiling Springs", "Spartanburg County", 1, 1/24),
    (29349, "Inman", "Spartanburg County", 1, 1/24),
    (29360, "Laurens", "Laurens County", 1, 1/24),
    (29605, "Greenville (South)", "Greenville County", 1, 1/24),
    (29615, "Greenville (East)", "Greenville County", 1, 1/24),
    (29625, "Anderson (West)", "Anderson County", 1, 1/24),
    (29640, "Easley", "Pickens County", 1, 1/24),
    (29653, "Honea Path", "Anderson / Abbeville County", 1, 1/24),
    (29687, "Taylors", "Greenville County", 1, 1/24),
    (97045, "Oregon City (Out of Area)", "Clackamas County, OR", 1, 1/24),
]

for idx, zr in enumerate(zip_details, start=next_row_geo2+2):
    ws_geo.row_dimensions[idx].height = 20
    is_zebra = (idx % 2 == 1)
    for c_idx, val in enumerate(zr, start=1):
        cell = ws_geo.cell(row=idx, column=c_idx, value=val)
        cell.font = FONT_DATA
        cell.border = BORDER_THIN
        if is_zebra:
            cell.fill = FILL_ZEBRA
        if c_idx == 1:
            cell.alignment = ALIGN_CENTER
        elif c_idx == 4:
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_INT
        elif c_idx == 5:
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_PCT
        else:
            cell.alignment = ALIGN_LEFT

for col in range(1, 15):
    ws_geo.column_dimensions[get_column_letter(col)].width = STD_HEADERS[col-1][2]

# ==========================================
# 5. TAB: EMPLOYMENT & FAMILY
# ==========================================
ws_emp = wb.create_sheet(title='Employment & Family')
ws_emp.views.sheetView[0].showGridLines = True
ws_emp.freeze_panes = 'B5'
ws_emp.cell(row=2, column=1, value="Caregiver Employment & Household Family Structure").font = FONT_TITLE
ws_emp.cell(row=3, column=1, value="Workforce status, stay-at-home primary caregiving, and child counts").font = FONT_SUBTITLE

rows_emp = compute_breakdown(comp['Employment Status'], [
    'Stay-at-home caregiver', 'Employed (full-time or part-time)',
    'None of the above / Other', 'On maternity/paternity leave', 'Student'
])
findings_emp = {
    'Stay-at-home caregiver': '✅ +26.1 pp; 41.7% stay-at-home caregivers (2.7× higher than online cohort)',
    'Employed (full-time or part-time)': '⬇ -34.1 pp; balanced workforce participation (45.8%)',
    'None of the above / Other': '⬆ +12.3 pp; represents diverse household roles',
    'On maternity/paternity leave': '↔ Low incidence',
    'Student': '↔ Low incidence'
}
next_row_emp = render_table(ws_emp, 5, "Table 1: Caregiver Employment & Primary Role", rows_emp, total_label="Total Completed (100%)", finding_map=findings_emp)

rows_ch = compute_breakdown(comp['Number of Children'], [1, 2, 3, 4, 5, 7])
findings_ch = {
    '1': '⬇ -32.3 pp; single-child bias removed (online pool was 48.9% 1 child)',
    '2': '↔ 25.0% representation in recent cohort',
    '3': '⬆ +28.1 pp; 37.5% have 3 children (4.0× higher)',
    '4': '⬆ +11.4 pp; 12.5% have 4 children (11× higher)',
    '5': '⬆ +8.1 pp; 8.3% have 5 children (36× higher)',
    '7': '— Outlier in registry'
}
next_row_emp2 = render_table(ws_emp, next_row_emp, "Table 2: Number of Children in Household", rows_ch, total_label="Total Completed (100%)", finding_map=findings_ch)

# Family size tiers
def fam_tier(x):
    if x == 1:
        return '1 Child (Single-child household)'
    elif x == 2:
        return '2 Children'
    elif x >= 3:
        return '3+ Children (Large family)'
    return 'Other'

comp['Fam_Tier'] = comp['Number of Children'].apply(fam_tier)
rows_fam = compute_breakdown(comp['Fam_Tier'], [
    '1 Child (Single-child household)', '2 Children', '3+ Children (Large family)'
])
findings_fam = {
    '1 Child (Single-child household)': '⬇ -32.3 pp; de-clustered from online panel bias',
    '2 Children': '↔ -15.3 pp; 25.0% in recent wave',
    '3+ Children (Large family)': '✅ +47.6 pp; 58.3% of recent wave have 3+ children (5.4× higher)'
}
render_table(ws_emp, next_row_emp2, "Table 3: Household Family Size Tiers", rows_fam, total_label="Total Completed (100%)", finding_map=findings_fam)

for col in range(1, 15):
    ws_emp.column_dimensions[get_column_letter(col)].width = STD_HEADERS[col-1][2]

# ==========================================
# 6. TAB: CLINICAL & AGE PROFILE
# ==========================================
ws_clin = wb.create_sheet(title='Clinical & Age Profile')
ws_clin.views.sheetView[0].showGridLines = True
ws_clin.freeze_panes = 'B5'
ws_clin.cell(row=2, column=1, value="Clinical Disability Indicators & Caregiver Age").font = FONT_TITLE
ws_clin.cell(row=3, column=1, value="Special needs diagnosis, autism diagnosis counts, and caregiver age brackets").font = FONT_SUBTITLE

rows_sn = compute_breakdown(comp['Children Special Needs/Disability'], ['Yes', 'No'])
findings_sn = {
    'Yes': '✅ +21.1 pp; exactly mirrors real CAN Registry (46.3% vs 45.8% recent)',
    'No': '⬇ Less generic population skew (-21.1 pp)'
}
next_row_clin = render_table(ws_clin, 5, "Table 1: Child with Special Needs / Disability Status", rows_sn, total_label="Total Completed (100%)", finding_map=findings_sn)

rows_aut = compute_breakdown(comp['Autistic Children Count'], [0, 1, 2, 3])
findings_aut = {
    '0': '⬆ +34.8 pp; broad caregiver population without autism diagnosis (62.5%)',
    '1': '⬇ -37.7 pp; prior online bots clustered at 71.1% clicking 1 child',
    '2': '↔ Plausible clinical incidence (4.2%)',
    '3': '— Rare outlier'
}
next_row_clin2 = render_table(ws_clin, next_row_clin, "Table 2: Autistic Children Count", rows_aut, total_label="Total Completed (100%)", finding_map=findings_aut)

# Age brackets
comp['Age_Num'] = pd.to_numeric(comp['Caregiver Age'], errors='coerce')
bins = [0, 24, 29, 34, 39, 44, 100]
labels = ['18–24 years', '25–29 years', '30–34 years', '35–39 years', '40–44 years', '45+ years']
comp['Age_Bracket'] = pd.cut(comp['Age_Num'], bins=bins, labels=labels)
rows_age = compute_breakdown(comp['Age_Bracket'], labels)
findings_age = {
    '18–24 years': '↔ Low young adult representation (4.2%)',
    '25–29 years': '⬇ -5.5 pp; 20.8% in recent wave',
    '30–34 years': '⬇ -8.6 pp; 29.2% in recent wave',
    '35–39 years': '⬆ +7.4 pp; 29.2% in recent wave',
    '40–44 years': '⬆ +7.9 pp; 12.5% in recent wave',
    '45+ years': '↔ 4.2% in recent wave'
}
next_row_clin3 = render_table(ws_clin, next_row_clin2, "Table 3: Caregiver Age Brackets", rows_age, total_label="Valid Age Records", finding_map=findings_age)

# Table 4: Age Statistics
ws_clin.cell(row=next_row_clin3, column=1, value="Table 4: Caregiver Age Descriptive Statistics").font = FONT_SECTION
headers_age_stats = ["Metric", "CAN Registry (4797)", "Global Online (4581)", "ICIS Board (4931)", "Prior Combined", "★ Recent Wave (5749 446+)", "Comparison Notes"]
ws_clin.row_dimensions[next_row_clin3+1].height = 25
for c_idx, h in enumerate(headers_age_stats, start=1):
    cell = ws_clin.cell(row=next_row_clin3+1, column=c_idx, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER_RECENT if '5749' in h else FILL_HEADER_NAVY
    cell.alignment = ALIGN_HEADER
    cell.border = BORDER_THIN

age_stats_data = [
    ("Valid N", 67, 1296, 4, 1367, 24, "All 24 completed surveys in 5749 have valid ages"),
    ("Mean Age (years)", 38.0, 31.3, 37.8, 31.6, 34.0, "Recent wave is slightly older than online baseline"),
    ("Median Age (years)", 37.0, 32.0, 37.0, 32.0, 33.5, "Plausible pediatric caregiver median"),
    ("Standard Deviation", 6.9, 7.3, 4.6, 7.4, 5.9, "Tighter, more consistent distribution in recent cohort"),
    ("Minimum Age", 26, -1, 33, -1, 24, "Online baseline had bot error (-1); recent min is valid (24)"),
    ("Maximum Age", 68, 65, 44, 68, 48, "Realistic upper range for active pediatric caregivers"),
    ("Interquartile Range (IQR)", "33.5 – 41.5", "28.0 – 35.0", "35.2 – 39.5", "28.0 – 35.0", "29.8 – 36.2", "Core 50% of recent cohort falls between 30 and 36"),
]

for idx, asr in enumerate(age_stats_data, start=next_row_clin3+2):
    ws_clin.row_dimensions[idx].height = 20
    is_zebra = (idx % 2 == 1)
    for c_idx, val in enumerate(asr, start=1):
        cell = ws_clin.cell(row=idx, column=c_idx, value=val)
        cell.font = FONT_DATA
        cell.border = BORDER_THIN
        if is_zebra:
            cell.fill = FILL_ZEBRA
        if c_idx == 6:
            cell.font = FONT_DATA_BOLD
            cell.fill = FILL_RECENT_DATA
        if c_idx in [2, 3, 4, 5, 6] and isinstance(val, (int, float)):
            cell.alignment = ALIGN_RIGHT
            cell.number_format = '0.0' if isinstance(val, float) else NUM_INT
        else:
            cell.alignment = ALIGN_LEFT

for col in range(1, 15):
    ws_clin.column_dimensions[get_column_letter(col)].width = STD_HEADERS[col-1][2]

# ==========================================
# 7. TAB: GENDER & SCREENING
# ==========================================
ws_gen = wb.create_sheet(title='Gender & Data Integrity')
ws_gen.views.sheetView[0].showGridLines = True
ws_gen.freeze_panes = 'B5'
ws_gen.cell(row=2, column=1, value="Caregiver Gender Identity & Bot Screening Outcomes").font = FONT_TITLE
ws_gen.cell(row=3, column=1, value="Gender, pronouns, and pipeline payment approval status").font = FONT_SUBTITLE

rows_gen = compute_breakdown(comp['Caregiver Gender'], ['Woman', 'Man', 'Non-binary', 'Prefer not to answer'])
findings_gen = {
    'Woman': '✅ 100% women (+35.2 pp); matches primary pediatric caregiver reality',
    'Man': '⬇ -35.0 pp; prior online survey had high male bot clustering',
    'Non-binary': '— Low incidence',
    'Prefer not to answer': '— Low incidence'
}
next_row_gen = render_table(ws_gen, 5, "Table 1: Caregiver Gender Identity", rows_gen, total_label="Total Completed (100%)", finding_map=findings_gen)

rows_pro = compute_breakdown(comp['Caregiver Pronouns'], ['She/Her/Hers', 'He/Him/His', 'They/Them/Theirs'])
findings_pro = {
    'She/Her/Hers': '✅ 91.7% in recent cohort (+47.0 pp); aligns with registry cohort',
    'He/Him/His': '⬇ -43.0 pp; online bot cohort was 51.3% He/Him',
    'They/Them/Theirs': '— 0% in recent wave'
}
next_row_gen2 = render_table(ws_gen, next_row_gen, "Table 2: Caregiver Pronouns", rows_pro, total_label="Total Completed (100%)", finding_map=findings_pro)

rows_scr = compute_breakdown(comp['Payment Status'], ['Pay Now', 'Manual Review', 'Do Not Pay'])
findings_scr = {
    'Pay Now': '✅ +45.9 pp; 70.8% approved real respondents (high data integrity)',
    'Manual Review': '⬇ -12.4 pp; 25.0% in manual review queue',
    'Do Not Pay': '✅ -33.5 pp; flagged bots reduced by 89% (only 1 flagged record in recent wave)'
}
render_table(ws_gen, next_row_gen2, "Table 3: Survey Screening Integrity & Payment Recommendation", rows_scr, total_label="Total Completed (100%)", finding_map=findings_scr)

for col in range(1, 15):
    ws_gen.column_dimensions[get_column_letter(col)].width = STD_HEADERS[col-1][2]

# Save workbook
os.makedirs(OUTPUT_DIR, exist_ok=True)
wb.save(OUTPUT_EXCEL)
shutil.copy2(OUTPUT_EXCEL, ROOT_COPY)

print(f"Successfully generated Excel workbook at:")
print(f"  {OUTPUT_EXCEL}")
print(f"  {ROOT_COPY}")
print(f"File size: {os.path.getsize(OUTPUT_EXCEL):,} bytes")
print("Sheets:", wb.sheetnames)
