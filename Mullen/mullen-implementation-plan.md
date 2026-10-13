# Mullen Paper-Layout Replication and Automated Scoring Plan

## Confirmed current structure

The current electronic setup has two instruments:

1. **Mullen Items** — metadata followed by Gross Motor, Visual Reception, Fine Motor, Receptive Language, and Expressive Language item scores.
2. **Mullen Scores** — raw score, T score, percentile rank, descriptive category, and age equivalent, with a second adjusted-for-prematurity block.

The paper score sheet uses a single matrix:

| Scale | Raw Score | T Score | Percentile | Descriptive Category | Age Equivalent |
|---|---:|---:|---:|---|---|
| Gross Motor | | | | | |
| Visual Reception | | | | | |
| Fine Motor | | | | | |
| Receptive Language | | | | | |
| Expressive Language | | | | | |

It also contains Participant ID, Visit Month, DOB, Date of Evaluation, Chronological Age, Gestational Age at Birth, Expected DOB, Adjusted Age, and Norms Used to Score.

## Target design

Build one paper-faithful workflow with:

1. **Paper header** — Participant ID, Visit Month, DOB, Evaluation Date, Examiner, Data Entry RA, and Date Entered.
2. **Item record** — five scale sections in paper order, with item number, item wording, valid score choices, and optional administration note.
3. **Scoring worksheet** — exact six-column paper matrix; calculated fields are read-only but preserve paper labels.
4. **Prematurity panel** — gestational age, expected DOB, chronological age, adjusted age, norm age, norms version, and adjustment status.

Store each item response once. Screen, print, export, raw-score calculation, and standard-score conversion must use that same authoritative value.

## Data model

### Assessment metadata

- `participant_id`
- `visit_month`
- `date_of_birth`
- `evaluation_date`
- `examiner`
- `data_entry_ra`
- `date_entered`
- `gestational_age_weeks`
- `gestational_age_days`
- `expected_dob`
- `chronological_age_months`
- `adjusted_age_months`
- `norm_age_used_months`
- `prematurity_adjustment_applied`
- `norms_version`

### Item response

- `scale_code`: GM, VR, FM, RL, EL
- `item_number`
- `item_label`
- `score_min`
- `score_max`
- `item_score`
- `administered_flag`
- `source_or_override`
- `override_reason`

### Versioned norm table

```text
norms_version
scale_code
norm_age_months
raw_score_min
raw_score_max
t_score
percentile_rank
age_equivalent_months
descriptive_category
source_page
verified_by
verified_date
```

Use one row for every valid age-band × scale × raw-score mapping. Enforce uniqueness and reject overlapping raw-score ranges. Do not hard-code norm values in UI branching logic.

## Calculation specification

### Age calculation

1. Calculate chronological age from DOB through evaluation date using complete calendar months; retain residual days for audit/display.
2. Calculate prematurity as `max(0, 40 weeks - gestational age at birth)`.
3. Calculate expected DOB by adding prematurity duration to DOB.
4. Calculate adjusted age from expected DOB through evaluation date.
5. Apply adjustment only under the approved study rule and cutoff.
6. Store chronological age, adjusted age, selected norm age, adjustment decision, and norms version.

### Raw score

```text
raw_score = sum(item scores included under validated basal, ceiling,
reversal, discontinuation, and unadministered-item rules)
```

Until those rules are verified from the authorized manual, expose only `provisional_raw_score = sum(entered item scores)` and do not label it final.

### Raw-to-standard lookup

```text
norm_row = norm table row where
  norms_version = selected version
  and scale_code = current scale
  and norm_age_months = selected norm age
  and raw_score is between raw_score_min and raw_score_max

T score              = norm_row.t_score
Percentile rank      = norm_row.percentile_rank
Age equivalent       = norm_row.age_equivalent_months
Descriptive category = norm_row.descriptive_category
```

Do not estimate T scores or percentiles with a generic normal-distribution formula. Use the authorized age-specific conversion table.

## Current-to-paper mapping

| Current field group | Paper destination | Implementation |
|---|---|---|
| Participant/evaluation metadata | Paper header | Retain; reorder to match paper |
| GM/VR/FM/RL/EL item fields | Five item sections | Preserve valid score ranges; add item numbers and compact rows |
| Raw score | Score matrix | Auto-calculate after rule validation |
| T score | Score matrix | Age-specific norm lookup; read-only |
| Percentile | Score matrix | Norm lookup; read-only |
| Descriptive category | Score matrix | Authorized lookup/rule; read-only |
| Age equivalent | Score matrix | Norm lookup; read-only |
| Adjusted duplicate score block | Prematurity panel | Recalculate from the same raw scores using adjusted norm age |
| Chronological/gestational/expected/adjusted age | Footer/prematurity panel | Auto-calculate and audit |
| Norms Used to Score | Footer | Auto-fill version, norm age, and adjustment status |

## Implementation checklist

### Source verification

- [ ] Preserve read-only copies of current exports and paper sources.
- [ ] Assign a version identifier to the authorized scoring manual and norm tables.
- [ ] Confirm item numbers, wording, score range, basal, ceiling, reversal, discontinuation, and unadministered-item handling.
- [ ] Confirm the prematurity-adjustment formula and maximum adjustment age.
- [ ] Have a qualified Mullen assessor verify all transcribed rules and tables.

### Paper-faithful UI

- [ ] Match the paper header order, labels, grouping, and width.
- [ ] Match all scale and item ordering.
- [ ] Limit each item to its valid score options.
- [ ] Recreate the six-column score matrix exactly.
- [ ] Add age/prematurity fields in the paper location.
- [ ] Visually distinguish calculated/read-only values without renaming paper fields.
- [ ] Add Letter-size print CSS, stable margins, page numbers, participant ID footer, controlled page breaks, and no clipped rows.
- [ ] Validate screen and print-to-PDF output.

### Calculation engine

- [ ] Validate dates and reject evaluation dates before DOB.
- [ ] Calculate chronological age, expected DOB, adjusted age, and selected norm age.
- [ ] Validate item ranges before save.
- [ ] Calculate raw scores from one authoritative response dataset.
- [ ] Add basal/ceiling/reversal logic only after manual verification.
- [ ] Import authorized norms into the versioned lookup table.
- [ ] Convert raw scores by exact scale × norm-age × raw-score lookup.
- [ ] Produce chronological-age and adjusted-age outputs from the same raw scores.
- [ ] Auto-populate `Norms Used to Score`.
- [ ] Require a reason and preserve original values for overrides.

### Validation

- [ ] Unit-test age boundaries and calendar-month transitions.
- [ ] Unit-test all item-score minimums, maximums, and invalid values.
- [ ] Test minimum/maximum raw scores in every norm-age band.
- [ ] Block final scoring when norm rows are missing, duplicated, or overlapping.
- [ ] Compare automated outputs with independently hand-scored cases.
- [ ] Test prematurity cases on both sides of the adjustment cutoff.
- [ ] Perform independent double scoring and discrepancy reconciliation.
- [ ] Save verified cases as regression fixtures.

### Deployment

- [ ] Separate provisional and verified/final scores.
- [ ] Restrict norm-table editing to authorized administrators.
- [ ] Audit item edits, engine version, norms version, adjustment status, overrides, scorer, and timestamps.
- [ ] Lock verified records unless explicitly reopened.
- [ ] Pilot with research staff before production.
- [ ] Confirm licensing and study-governance approval for electronic item text and norm tables.

## Acceptance criteria

- Printed output matches the controlled paper form's label order, table structure, page size, and grouping.
- Each response is stored once and is consistent across screen, print, export, and scoring.
- Recalculation is deterministic and records engine and norms versions.
- Every calculated output traces to item responses, raw score, norm age, norm row, and adjustment decision.
- Automated results match independently verified hand scoring across the validation set.
- Missing rules or norms fail closed with “cannot score”; the system never guesses.

## Inputs required for final build

1. Authorized readable norm conversion tables.
2. Official basal, ceiling, reversal, and discontinuation rules for all five scales.
3. The project-specific prematurity-adjustment rule and cutoff.
4. Confirmation that the target is REDCap, a standalone web application, or both.
5. Permission to reproduce item wording and norm tables electronically.
