# REDCap CSBS BS Formula Spec (Sandbox)

Project: NANO Lab Assessments & Double Data Entry- Sandbox (PID 6207)
Instrument: CSBS BS (`csbs_bs`)

## Auto-calculated raw cluster formulas
- `csbsbs_emotionraw = round(sum([csbsbs_scale1],[csbsbs_scale2],([csbsbs_scale3]*3)),0)`
- `csbsbs_comraw = round(sum(([csbsbs_scale4]/3),[csbsbs_scale5],[csbsbs_scale6],[csbsbs_scale7]),0)`
- `csbsbs_gesraw = round(sum(([csbsbs_scale8]*2),[csbsbs_scale9]),0)`
- `csbsbs_soundsraw = round(sum([csbsbs_scale10],([csbsbs_scale11]*2)),0)`
- `csbsbs_wordsraw = round(sum([csbsbs_scale12],([csbsbs_scale13]/2),[csbsbs_scale14],[csbsbs_scale15]),0)`
- `csbsbs_underraw = round(sum(([csbsbs_scale16_1]*3),([csbsbs_scale16_2]*3),([csbsbs_scale16_3]*3)),0)`
- `csbsbs_objectraw = round(sum([csbsbs_scale17],[csbsbs_scale18],[csbsbs_scale19],[csbsbs_scale20]),0)`

## Auto-calculated composite and total formulas
- `csbsbs_socialcompositecalc = sum([csbsbs_emotionraw],[csbsbs_comraw],[csbsbs_gesraw])`
- `csbsbs_speechcompositecalc = sum([csbsbs_soundsraw],[csbsbs_wordsraw])`
- `csbsbs_symboliccompositecalc = sum([csbsbs_underraw],[csbsbs_objectraw])`
- `csbsbs_totalrawcalc = sum([csbsbs_socialcompositecalc],[csbsbs_speechcompositecalc],[csbsbs_symboliccompositecalc])`

## Labeling standard applied
All score outputs above are labeled with the `AUTO-CALCULATED:` prefix in REDCap so users can distinguish computed fields from manually entered normative fields.

## Validation state
- API token mapped to PID 6207 and validated.
- Record-level verification: 1767 exported rows, 5533 score comparisons, 0 mismatches.
- Result: REDCap CSBS BS formulas are aligned with source notebook logic.
