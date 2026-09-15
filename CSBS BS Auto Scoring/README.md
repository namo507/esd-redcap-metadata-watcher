# CSBS BS Auto Scoring

This folder keeps REDCap scoring governance assets for the CSBS BS instrument in the NANO Lab Assessments & Double Data Entry sandbox.

## Purpose
- Keep validation logic and REDCap scoring specs in one place.
- Treat REDCap as the final implementation target.
- Use notebook/scripts to verify REDCap formulas and labels stay aligned to approved worksheet logic.

## Contents
- `source/REDCap_CSBS_EDI_Scoring_Audit_source.ipynb`: source audit notebook provided by user.
- `source/csbs_edi_record_summary_source.xlsx`: source workbook provided by user.
- `notebooks/csbs_bs_redcap_validation.ipynb`: focused CSBS BS validation notebook for PID 6207.
- `reference/redcap_csbs_bs_formula_spec.md`: current REDCap formula, label, and deployment state.

## REDCap status applied in sandbox (PID 6207)
- All 11 CSBS BS calculated fields validated against notebook logic with zero mismatches on scoreable records.
- Auto-score labels updated in REDCap to explicitly show they are system-calculated:
  - `AUTO-CALCULATED: Emotion and Eye Gaze Weighted Raw Score`
  - `AUTO-CALCULATED: Communication Weighted Raw Score`
  - `AUTO-CALCULATED: Gestures Weighted Raw Score`
  - `AUTO-CALCULATED: Sounds Weighted Raw Score`
  - `AUTO-CALCULATED: Words Weighted Raw Score`
  - `AUTO-CALCULATED: Understanding Weighted Raw Score`
  - `AUTO-CALCULATED: Object Use Weighted Raw Score`
  - `AUTO-CALCULATED: Social Composite Score`
  - `AUTO-CALCULATED: Speech Composite Score`
  - `AUTO-CALCULATED: Symbolic Composite Score`
  - `AUTO-CALCULATED: CSBS BS Total Raw Score`

## Operational note
When logic is updated in future, update REDCap formulas/labels first, then run the validation notebook in `notebooks/` to re-confirm alignment.
