# REDCap CSBS Caregiver Formula Spec (Sandbox)

Project: NANO - CSBS Caregiver / EDI Scoring Sandbox (PID 6205)
Instrument: CSBS Caregiver (`csbs_caregiver`)
24-month event unique name: `24_months_arm_1`

## Core raw cluster formulas (existing, retained)
- Emotion and eye gaze: `roundup(sum([csbscg1]+[csbscg2]+[csbscg3]+[csbscg4]+(2-[csbscg5])+[csbscg6]+[csbscg7]+[csbscg8]))`
- Communication: `roundup(sum([csbscg9]+[csbscg10]+[csbscg11]+[csbscg12]+[csbscg13]+[csbscg14]+[csbscg15]+[csbscg16]+[csbscg17]+[csbscg18]))`
- Gestures: `roundup(sum([csbscg19]+[csbscg20_1]+[csbscg20_2]+[csbscg20_3]+[csbscg20_4]+[csbscg20_5]+[csbscg20_6]+[csbscg20_7]+[csbscg20_8]+[csbscg20_9]+[csbscg20_10]))`
- Sounds: `roundup(sum([csbscg21]+[csbscg22]+[csbscg23_1]+[csbscg23_2]+[csbscg23_3]+[csbscg23_4]+[csbscg23_5]+[csbscg23_6]+[csbscg23_7]+[csbscg23_8]+[csbscg23_9]+[csbscg23_10]+[csbscg24]))`
- Words: existing half-weighted item26 matrix formula retained.
- Understanding: existing half-weighted item32 matrix formula retained.
- Object use: existing half-weighted item35/37/40/41 matrix formula retained.

## Composite/total hierarchy (updated)
- `csbs_socialcomposite = [csbs_emotionandeyegaze]+[csbs_communication]+[csbs_gestures]`
- `csbs_speechcomposite = [csbs_sounds]+[csbs_words]`
- `csbs_symboliccomposite = [csbs_understanding]+[csbs_objectuse]`
- `cbscg_totalscore = [csbs_socialcomposite]+[csbs_speechcomposite]+[csbs_symboliccomposite]`

This prevents recomputation and rounding-order drift in Symbolic and Total scores.

## New auto norm field
- Variable: `csbs_totalss_auto`
- Label: `Total Standard Score - Auto-calculated (23-24 month norms)`
- Type: calculated field
- Branching logic: `[event-name]='24_months_arm_1'`
- Calculation: nested `if()` mapping from `cbscg_totalscore` over full raw range 0-139.
- Guards:
  - non-24-month event -> blank
  - blank `cbscg_totalscore` -> blank
  - raw < 0 or raw > 139 -> blank

## Legacy compatibility
- `csbs_totalss` left unchanged (manual legacy field).
- `csbs_totalper` left unchanged (manual legacy field).
