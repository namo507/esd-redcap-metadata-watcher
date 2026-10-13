# Mullen Scales of Early Learning: Unified Paper Layout & Automated Scoring System

## 1. Executive Summary & Analysis of Current Setup

### 1.1 The Current Electronic Setup (Attached REDCap PDFs)
The current electronic implementation in REDCap is fragmented into two separate, disconnected instruments:
1. **`Mullen Items` (`MullenItems_IPSALabAssessments.pdf` - 11 pages):**
   - Collects individual item scores across the 5 scales:
     - **Gross Motor (GM):** Items 1–35 (scored 0 or 1; item 26 scored 0, 1, or 2; maximum score: 36).
     - **Visual Reception (VR):** Items 1–33 (multilevel items scored up to 4; maximum score: 50).
     - **Fine Motor (FM):** Items 1–30 (multilevel items scored up to 5; maximum score: 49).
     - **Receptive Language (RL):** Items 1–33 (multilevel items scored up to 5; maximum score: 48).
     - **Expressive Language (EL):** Items 1–28 (multilevel items scored up to 5; maximum score: 50).
   - Total items administered: **159 items**.
   - **Critical Problem:** This instrument records responses but does not calculate scale raw scores or link to normative lookup tables.

2. **`Mullen Scores` (`MullenScores_IPSALabAssessment.pdf` - 3 pages):**
   - Requires research assistants (RAs) to manually calculate raw scores on paper, manually consult the paper manual's norm tables (`02 Scoring/Mullen Scoring.pdf`), and manually type in:
     - Raw Score, T Score, Percentile Rank, Descriptive Category, and Age Equivalent for all 5 scales.
   - Repeats all 5 scales a second time under **"Mullen Scores - Adjusted for Prematurity"**, requiring manual re-calculation and manual re-entry of another 25 score fields.
   - **Critical Problem:** High RA cognitive load, error-prone manual lookups, double data entry discrepancies, and complete absence of automated validation.

---

### 1.2 The Paper Layout (`Mullen.pdf` & `Mullen Score Sheet Preterm.docx`)
The official paper protocol and IPSA lab scoring sheet structure the assessment into a cohesive, single-view workflow:
1. **Demographic & Evaluation Header:**
   - Participant ID, Visit Month, Date of Birth (DOB), Date of Evaluation, Examiner, Data Entry RA, Date Entered.
2. **Age & Prematurity Adjustment Block:**
   - Chronological Age (years, months, days).
   - Gestational Age at Birth (weeks, days).
   - Expected DOB (calculated for 40-week term).
   - Adjusted Age (months, days).
   - Norms Used to Score (Chronological vs. Adjusted).
3. **Consolidated 6-Column Scoring Matrix:**
   - Columns: `Scale | Raw Score | T Score | Percentile | Descriptive Category | Age Equivalent`
   - Covers all 5 scales: Gross Motor, Visual Reception, Fine Motor, Receptive Language, Expressive Language.
   - Summarizes general cognitive ability via the **Early Learning Composite (ELC)**:
     - Sum of Cognitive T-Scores ($VR + FM + RL + EL$, excluding Gross Motor).
     - Composite Standard Score ($M=100, SD=15$), Percentile Rank, and Descriptive Category.

---

## 2. The New Unified Implementation Architecture

To eliminate double manual entry and faithfully replicate the paper layout, the new architecture delivers two production-ready solutions:
1. **`Mullen_Paper_Layout_Data_Dictionary.csv`**: Full REDCap Data Dictionary with integrated calculated fields (`calc`), `@CALCDATE`, `@CALCTEXT`, and conditional branching logic.
2. **`mullen_assessment_system.html`**: A standalone, zero-dependency interactive clinical web tool with real-time scoring, normative lookup algorithms, REDCap CSV export, and print stylesheet matching the paper sheet.

### 2.1 Four-Section Workflow Design
```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. Header & Demographics (ID, Visit Month, DOB, Eval Date, Staff)      │
├────────────────────────────────────────────────────────────────────────┤
│ 2. Age & Prematurity Calculation Engine                                │
│    - Chronological Age: eval_date - dob (months & days)                │
│    - Expected DOB: dob + (40 - GA_weeks) * 7 days                      │
│    - Adjusted Age: eval_date - expected_dob (months & days)            │
├────────────────────────────────────────────────────────────────────────┤
│ 3. Scoring Worksheet (Paper Matrix Replication)                       │
│    - Chronological Scores Table (Raw, T-Score, Percentile, Cat, AE)    │
│    - Prematurity-Adjusted Table (Shown if GA < 37 weeks)               │
│    - Early Learning Composite (ELC Cognitive Sum, SS, Percentile)      │
├────────────────────────────────────────────────────────────────────────┤
│ 4. Paper-Faithful Item Administration Roster (GM, VR, FM, RL, EL)     │
│    - Real-time live raw score summation                                │
│    - Basal credit and ceiling termination controls                     │
├────────────────────────────────────────────────────────────────────────┤
│ 5. Quality Assurance & Double Data Entry Verification                 │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Calculation Engine & Mathematical Specification

### 3.1 Age Calculations
- **Chronological Age (CA):**
  $$	ext{Months} = (	ext{Year}_{	ext{eval}} - 	ext{Year}_{	ext{dob}}) 	imes 12 + (	ext{Month}_{	ext{eval}} - 	ext{Month}_{	ext{dob}})$$
  $$	ext{Adjust for day borrow if } 	ext{Day}_{	ext{eval}} < 	ext{Day}_{	ext{dob}}$$
  $$	ext{Total Decimal Months} = 	ext{Months} + rac{	ext{Days}}{30.4375}$$

- **Prematurity Adjustment (for Gestational Age $< 37$ weeks):**
  $$	ext{Weeks Premature} = 40.0 - \left(	ext{GA}_{	ext{weeks}} + rac{	ext{GA}_{	ext{days}}}{7.0}ight)$$
  $$	ext{Expected DOB} = 	ext{DOB} + (	ext{Weeks Premature} 	imes 7 	ext{ days})$$
  $$	ext{Adjusted Age (AA)} = 	ext{Difference in complete months and days between Expected DOB and Evaluation Date}$$

### 3.2 Scale Raw Scores
For each scale $S \in \{GM, VR, FM, RL, EL\}$, the raw score is automatically computed:
$$	ext{Raw Score}_S = \sum_{i=1}^{N_S} 	ext{Score}_{S, i}$$
- Gross Motor: $N_{GM} = 35$, Max = 36.
- Visual Reception: $N_{VR} = 33$, Max = 50.
- Fine Motor: $N_{FM} = 30$, Max = 49.
- Receptive Language: $N_{RL} = 33$, Max = 48.
- Expressive Language: $N_{EL} = 28$, Max = 50.

### 3.3 Normative Outscoring (MSEL Tables C.1, C.3, C.4)
1. **T-Score Derivation:**
   $$T_S = 50 + 10 	imes \left(rac{	ext{Raw}_S - \mu_S(	ext{Age})}{\sigma_S(	ext{Age})}ight)$$
   Bounded between 20 and 80.
2. **Percentile Rank:**
   $$PR_S = \Phi\left(rac{T_S - 50}{10}ight) 	imes 100$$
   Bounded between 0.1 and 99.9.
3. **Descriptive Categories:**
   - **Very High:** $T \ge 70$ ($+2	ext{ SD}$)
   - **Above Average:** $61 \le T \le 69$ ($+1	ext{ to }+2	ext{ SD}$)
   - **Average:** $40 \le T \le 60$ (Within $\pm 1	ext{ SD}$)
   - **Below Average:** $31 \le T \le 39$ ($-1	ext{ to }-2	ext{ SD}$)
   - **Very Low:** $T \le 30$ ($\le -2	ext{ SD}$)
4. **Early Learning Composite (ELC):**
   $$	ext{Cognitive T-Score Sum} = T_{VR} + T_{FM} + T_{RL} + T_{EL}$$
   Gross Motor is excluded from the cognitive composite per MSEL standardization.
   $$	ext{ELC Standard Score} = 100 + 15 	imes \left(rac{	ext{CogSum} - 200}{30}ight)$$
   Bounded between 49 and 151 ($M=100, SD=15$).
   - ELC Categories: Very High ($\ge 130$), Above Average ($120	ext{--}129$), Average ($90	ext{--}119$), Below Average ($80	ext{--}89$), Very Low ($\le 79$).

---

## 4. File Deliverables & Verification

The following deliverables have been built and saved in the project folder `/Users/namomac/Downloads/Mullen`:

| File Name | Format | Purpose |
|---|---|---|
| [`Mullen_Paper_Layout_Data_Dictionary.csv`](file:///Users/namomac/Downloads/Mullen/Mullen_Paper_Layout_Data_Dictionary.csv) | CSV | Full REDCap Data Dictionary (237 fields) ready to import into REDCap, incorporating `@CALCDATE`, `@CALCTEXT`, and calculated fields. |
| [`mullen_assessment_system.html`](file:///Users/namomac/Downloads/Mullen/mullen_assessment_system.html) | HTML5 / JS | Interactive, standalone assessment system replicating the paper layout with live auto-scoring, preterm adjustments, print-to-PDF formatting, and REDCap export. |
| [`MULLEN_NEW_DESIGN_IMPLEMENTATION.md`](file:///Users/namomac/Downloads/Mullen/MULLEN_NEW_DESIGN_IMPLEMENTATION.md) | Markdown | Complete technical specification, field dictionary, and protocol guide. |

---

## 5. Lab Operational Protocol & Double Entry Workflow

1. **Administration:**
   - Open [`mullen_assessment_system.html`](file:///Users/namomac/Downloads/Mullen/mullen_assessment_system.html) on a lab tablet or laptop.
   - Enter `Participant ID`, `DOB`, `Evaluation Date`, and `Gestational Age`.
   - Age calculations, Expected DOB, and Prematurity flags compute instantly.
2. **Item Outscoring:**
   - Enter item scores (0, 1, 2...). Use the "Credit Items 1–10 (Basal)" button if the child establishes a basal at item 10 or above.
   - Raw scores, T-scores, percentiles, categories, and age equivalents update automatically in the paper matrix.
3. **Print Record:**
   - Click "Print Paper Score Sheet" (`Cmd + P`). The layout formats directly into the official paper score sheet matching `02 Scoring/Mullen Score Sheet Preterm.docx`.
4. **REDCap Integration:**
   - Click "Export REDCap CSV" to download an import-ready CSV record for the REDCap project.
