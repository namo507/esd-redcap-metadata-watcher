# CSBS Auto Scoring

This folder keeps REDCap scoring governance assets for the CSBS Caregiver sandbox.

## Purpose
- Keep validation code and references in one place.
- Treat REDCap as the implementation target.
- Use notebooks/scripts only to validate that REDCap formulas match approved logic.

## Contents
- `source/REDCap_CSBS_EDI_Scoring_Audit_source.ipynb`: source audit notebook provided by user.
- `source/csbs_edi_record_summary_source.xlsx`: source workbook provided by user.
- `notebooks/csbs_caregiver_redcap_validation.ipynb`: focused CSBS Caregiver validation notebook for REDCap checks.
- `reference/redcap_csbs_caregiver_formula_spec.md`: current REDCap scoring specification and deployment notes.

## REDCap status applied in sandbox (PID 6205)
- Composite and total hierarchy normalized to use child calculated fields:
  - `csbs_socialcomposite = [csbs_emotionandeyegaze]+[csbs_communication]+[csbs_gestures]`
  - `csbs_speechcomposite = [csbs_sounds]+[csbs_words]`
  - `csbs_symboliccomposite = [csbs_understanding]+[csbs_objectuse]`
  - `cbscg_totalscore = [csbs_socialcomposite]+[csbs_speechcomposite]+[csbs_symboliccomposite]`
- Added auto total standard score field:
  - Variable: `csbs_totalss_auto`
  - Label: `Total Standard Score - Auto-calculated (23-24 month norms)`
  - Event-scoped display via branching: `[event-name]='24_months_arm_1'`
  - Formula-scoped guard for non-24-month events, blanks, and out-of-range raw totals.

## Operational note
If logic needs updates later, update REDCap first, then regenerate validation results using the notebook in `notebooks/`.
